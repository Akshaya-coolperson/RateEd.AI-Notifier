#!/usr/bin/env python3
"""
RateEd.AI ALM Test Watcher
---------------------------
Logs into student.rateed.ai (via AWS Cognito, same as the site itself),
checks the "upcoming interviews/tests" API, and emails you (via Resend)
when:
  - a new test appears that you haven't been notified about yet
  - a test is ~1 day away
  - a test is ~1 hour away

State (which tests we've already emailed about) is kept in
data/seen_state.json so the script is safe to run repeatedly on a
schedule (e.g. GitHub Actions) without spamming duplicate emails.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Config (fixed values discovered from the site's own network requests)
# ---------------------------------------------------------------------------
COGNITO_REGION = "ap-south-1"
COGNITO_CLIENT_ID = "4r1bqa1dn8ea8jb4q1ndfvgtn5"
COGNITO_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

API_URL = "https://api.rateed.ai/students/v2/me/faculty-assessments/overview"

STATE_PATH = Path(__file__).parent / "data" / "seen_state.json"

# Reminder windows: (state_key, max_time_until_test, label)
REMINDER_WINDOWS = [
    ("reminded_1day", timedelta(days=1, hours=1), "1 day"),
    ("reminded_1hr", timedelta(hours=1, minutes=15), "1 hour"),
]


# ---------------------------------------------------------------------------
# Cognito login
# ---------------------------------------------------------------------------
def cognito_login(username: str, password: str) -> tuple[str, str]:
    """Returns (access_token, id_token) or raises with a clear error."""
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    }
    body = {
        "AuthFlow": "USER_PASSWORD_AUTH",
        "ClientId": COGNITO_CLIENT_ID,
        "AuthParameters": {
            "USERNAME": username,
            "PASSWORD": password,
        },
    }
    resp = requests.post(COGNITO_URL, headers=headers, json=body, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(
            f"Cognito login failed ({resp.status_code}): {resp.text[:500]}"
        )

    data = resp.json()

    if "ChallengeName" in data:
        raise RuntimeError(
            f"Cognito requires an extra step we don't handle: "
            f"{data['ChallengeName']} (e.g. NEW_PASSWORD_REQUIRED, MFA). "
            f"Log in manually on the site once to clear this, then retry."
        )

    result = data.get("AuthenticationResult")
    if not result:
        raise RuntimeError(f"Unexpected Cognito response: {json.dumps(data)[:500]}")

    return result["AccessToken"], result["IdToken"]


# ---------------------------------------------------------------------------
# RateEd API
# ---------------------------------------------------------------------------
def fetch_upcoming(access_token: str, id_token: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "x-id-token": id_token,
        "Accept": "application/json",
    }
    resp = requests.get(API_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("current_assessments", [])


# ---------------------------------------------------------------------------
# Helpers to pull useful info out of an assessment item
# ---------------------------------------------------------------------------
def get_item_id(item: dict) -> str:
    return str(item.get("assessment_id") or hash(json.dumps(item, sort_keys=True)))


def get_item_title(item: dict) -> str:
    course = item.get("course_name", "Unknown course")
    name = item.get("assessment_name", "Assessment")
    return f"{course} — {name}"


def parse_dt(value):
    """Parse an ISO datetime string (RateEd sends UTC with trailing 'Z')."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def get_item_datetime(item: dict):
    """The datetime we base reminder timing on: the assessment's start_time."""
    return parse_dt(item.get("start_time"))


IST = ZoneInfo("Asia/Kolkata")


def format_item_for_email(item: dict) -> str:
    title = get_item_title(item)
    start_dt = parse_dt(item.get("start_time"))
    end_dt = parse_dt(item.get("end_time"))
    start_str = start_dt.astimezone(IST).strftime("%A, %d %b %Y at %I:%M %p IST") if start_dt else item.get("start_time", "unknown")
    end_str = end_dt.astimezone(IST).strftime("%I:%M %p IST") if end_dt else item.get("end_time", "unknown")
    duration = item.get("duration_minutes", "?")
    proctored = "Yes" if item.get("is_proctored") else "No"

    return (
        f"<b>{title}</b><br>"
        f"Type: {item.get('type', 'unknown')}<br>"
        f"Start: {start_str}<br>"
        f"End: {end_str}<br>"
        f"Duration: {duration} minutes<br>"
        f"Proctored: {proctored}"
    )


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"notified_new": [], "reminded_1day": [], "reminded_1hr": []}


def save_state(state: dict, current_ids: set[str]):
    # Prune ids that are no longer in the upcoming list (test happened / cancelled)
    for key in ("notified_new", "reminded_1day", "reminded_1hr"):
        state[key] = [i for i in state.get(key, []) if i in current_ids]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Email via Resend
# ---------------------------------------------------------------------------
def send_email(resend_api_key: str, to_email: str, subject: str, html: str):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": "RateEd Watcher <onboarding@resend.dev>",
            "to": [to_email],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"WARNING: failed to send email ({resp.status_code}): {resp.text[:500]}",
              file=sys.stderr)
    else:
        print(f"Email sent: {subject}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    names = ["RATEED_USERNAME", "RATEED_PASSWORD", "RESEND_API_KEY", "NOTIFY_EMAIL"]
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        sys.exit(f"ERROR: these secrets are empty or missing: {', '.join(missing)}")

    username = os.environ["RATEED_USERNAME"].strip()
    password = os.environ["RATEED_PASSWORD"]
    resend_key = os.environ["RESEND_API_KEY"].strip()
    notify_email = os.environ["NOTIFY_EMAIL"].strip()

    # Manual test mode: just send a test email and stop
    if os.environ.get("SEND_TEST_EMAIL", "").lower() == "true":
        send_email(
            resend_key,
            notify_email,
            subject="✅ RateEd Watcher test email",
            html="If you can read this, email delivery works. The watcher will email you when a test is scheduled and remind you 1 day and 1 hour before.",
        )
        return

    access_token, id_token = cognito_login(username, password)
    items = fetch_upcoming(access_token, id_token)

    print(f"Found {len(items)} upcoming item(s).")

    state = load_state()
    current_ids = set()
    now = datetime.now(timezone.utc)

    for item in items:
        item_id = get_item_id(item)
        current_ids.add(item_id)

        # 1) Brand new test notification
        if item_id not in state["notified_new"]:
            send_email(
                resend_key,
                notify_email,
                subject="🆕 New ALM test scheduled on RateEd",
                html=f"A new test just showed up on RateEd:<br><br>{format_item_for_email(item)}",
            )
            state["notified_new"].append(item_id)

        # 2) Time-based reminders
        dt = get_item_datetime(item)
        if dt:
            time_until = dt - now
            for state_key, window, label in REMINDER_WINDOWS:
                already_sent = item_id in state[state_key]
                if not already_sent and timedelta(0) < time_until <= window:
                    send_email(
                        resend_key,
                        notify_email,
                        subject=f"⏰ Reminder: ALM test in ~{label}",
                        html=f"Your test is coming up in about {label}:<br><br>{format_item_for_email(item)}",
                    )
                    state[state_key].append(item_id)

    save_state(state, current_ids)
    print("Done.")


if __name__ == "__main__":
    main()
