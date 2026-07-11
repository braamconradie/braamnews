# Daily News Briefing

Fetches news and market data every morning, summarizes it with Claude, and emails
you a briefing via Gmail. Runs automatically on GitHub Actions — no server to
maintain.

## What it covers

Defined in [`topics.yaml`](topics.yaml), in this priority order:

1. **NZ Energy Market** — Electricity Authority / Commerce Commission / MBIE,
   gentailer competitor moves (Meridian, Contact, Genesis, Manawa, Nova),
   wholesale market events.
2. **Platform & Billing Tech** — Kraken/Octopus Energy, Gentrack, Salesforce
   Energy & Utilities, Kaluza.
3. **Telco Convergence** — One NZ, Spark, 2degrees.
4. **AI & Tooling** — Anthropic/Claude, agentic AI, enterprise AI in utilities.
5. **Markets & Crypto** — live BTC/ETH prices, MSTR/BMNR quotes, NZD/USD (only
   shown if it moved >0.5%), plus related news with AI-written one-line drivers.
6. **Consciousness** — a short, original inspirational reflection.

News comes from free Google News RSS searches (no API key). Market data comes
from CoinGecko (crypto) and Yahoo Finance (stocks/FX), also no API key needed.
Claude (Anthropic API) writes the actual summaries and reflection.

Edit `topics.yaml` any time to add/remove sections or change search queries —
no code changes needed for that.

## One-time setup

### 1. Get an Anthropic API key

Create one at [console.anthropic.com](https://console.anthropic.com/settings/keys)
if you don't already have one.

### 2. Create a Gmail App Password

This lets the script send mail as you without your main Google password.

1. Turn on 2-Step Verification on your Google account, if not already on:
   https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Create an app password (name it e.g. "daily-briefing"), copy the 16-character
   code it gives you (spaces don't matter).

### 3. Add GitHub repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add all of these:

| Secret name          | Value                                      |
|-----------------------|---------------------------------------------|
| `ANTHROPIC_API_KEY`   | your Anthropic API key                      |
| `GMAIL_ADDRESS`       | the Gmail address you generated the app password for |
| `GMAIL_APP_PASSWORD`  | the 16-character app password (no spaces)   |
| `RECIPIENT_EMAIL`     | where the briefing should be sent (e.g. `braam.conradie@gmail.com`) |

`RECIPIENT_EMAIL` can be the same address as `GMAIL_ADDRESS`, or different if you
want to send from one account and read in another.

### 4. Run it

The workflow is scheduled for **18:30 UTC daily** (≈6:30am NZ time, drifting by an
hour across daylight saving — see the comment in the workflow file if you want to
tighten that up). To test immediately instead of waiting:

- Go to the **Actions** tab → **Daily News Briefing** → **Run workflow**.
- Check the run logs for errors, and check your inbox.

## Local testing

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your real values
export $(grep -v '^#' .env | xargs)
python main.py
```

## Notes / limitations

- Google News RSS and Yahoo Finance's quote endpoint are both free and keyless,
  but unofficial — they can occasionally change shape or rate-limit. The code
  degrades gracefully (skips a source and logs a warning) rather than crashing
  the whole run, so a partial briefing still gets sent.
- Claude is instructed to only summarize what was actually fetched, and to say
  so plainly when a section has nothing worth reporting, rather than inventing
  content.
