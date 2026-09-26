# RateEd ALM Test Watcher

Checks student.rateed.ai every hour for newly scheduled ALM tests and
emails you:
- immediately when a new test is scheduled
- ~1 day before it
- ~1 hour before it

## How it works

1. Logs into RateEd's backend (AWS Cognito) using your RateEd username/password
   — the same login the website itself uses.
2. Calls the same "upcoming tests" API the site's dashboard calls.
3. Compares against `data/seen_state.json` (committed to this repo) to avoid
   sending duplicate emails.
4. Sends emails through [Resend](https://resend.com)'s free API, addressed to
   your Outlook inbox (or any address you choose).

## One-time setup

### 1. Create the repo
Push this folder to a **private** GitHub repository. (Private matters —
your login runs through GitHub Actions secrets, but keep the repo private
as a general good habit.)

### 2. Get a Resend API key
1. Sign up free at https://resend.com (100 emails/day free, no credit card).
2. Verify the email address you sign up with — this becomes your only
   allowed recipient unless you verify a domain, so **sign up with the
   same address you want the reminders sent to** (e.g. your Outlook address).
3. Dashboard → API Keys → Create API Key → copy it.

### 3. Add repo secrets
In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add these four:

| Secret name        | Value                                      |
|---------------------|---------------------------------------------|
| `RATEED_USERNAME`   | Your RateEd student login email             |
| `RATEED_PASSWORD`   | Your RateEd student login password          |
| `RESEND_API_KEY`    | The API key from step 2                     |
| `NOTIFY_EMAIL`      | The Outlook address you want emails sent to |

### 4. Enable Actions
Go to the **Actions** tab in your repo and enable workflows if prompted.
The workflow runs automatically every hour. You can also click
**"Check RateEd Schedule" → Run workflow** any time to test it manually.

## Notes / limitations

- If RateEd ever requires a one-time password reset or MFA challenge on
  login, the automated login will fail with a clear error in the Actions
  log (you'd need to log in manually on the site once to clear it, then
  the automation resumes working).
- The script guesses at field names for "test title" and "test date" from
  the API response, since no test was scheduled at the time this was
  built (nothing to inspect). If the first real notification email looks
  a bit rough (e.g. shows raw JSON instead of a clean date), send me that
  email and I'll tighten up the formatting.
- State is stored in `data/seen_state.json` and updated by the workflow
  itself (it commits back to the repo after each run) — don't edit that
  file by hand while the workflow is active.
