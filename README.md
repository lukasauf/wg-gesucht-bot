# WG-Gesucht Auto-Messenger

Sends a templated message to the newest listings on your filtered
[wg-gesucht.de](https://www.wg-gesucht.de/) search, on a schedule (via GitHub
Actions cron). No official API or token exists — it logs in with your normal
**email + password** through a headless browser (Playwright) and sends messages
exactly like a real user would.

> ⚠️ **Heads up:** Automated access likely violates WG-Gesucht's Terms of
> Service and may lead to account suspension or IP bans. The website can change
> at any time. Use at your own risk, keep the volume low, and be ready to
> maintain it.

## How authentication works

There is **no API key**. The bot opens the real website in a headless Chromium
browser, types your email/password into the login form, and the browser holds
the full logged-in session for sending messages. Your credentials live only in:

- **GitHub Actions secrets** (for the scheduled runs), and/or
- a local **`.env`** file (for testing on your machine).

They are never committed to the repo.

## Files

| File | Purpose |
| --- | --- |
| [wg_gesucht.py](wg_gesucht.py) | The bot: login → fetch listings → send messages |
| [config.yaml](config.yaml) | Your search URL and run options |
| [message.txt](message.txt) | The message body (`{recipient}` is auto-filled) |
| [state.json](state.json) | Tracks already-contacted listings (prevents duplicates) |
| [.github/workflows/wg-bot.yml](.github/workflows/wg-bot.yml) | The twice-daily cron schedule |
| [.env.example](.env.example) | Template for local credentials |

## Setup

### 1. Configure your search and message

1. Go to wg-gesucht.de, apply all your filters, and copy the resulting URL into
   `search_url` in [config.yaml](config.yaml).
2. Edit [message.txt](message.txt) with your text. Use `{recipient}` where the
   recipient's name should go.

### 2. Run it locally (recommended first)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env        # then edit .env with your real credentials
set -a; source .env; set +a # load WG_EMAIL / WG_PASSWORD into the shell
python wg_gesucht.py
```

To watch the browser do its thing (useful for debugging), run with
`HEADLESS=0 python wg_gesucht.py`. Tip: set `max_listings_per_run: 1` in
[config.yaml](config.yaml) for your very first test so only one message goes
out, then check your WG-Gesucht inbox.

### 3. Run it on a schedule with GitHub Actions

1. Push this folder to a **private** GitHub repository.
2. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, add:
   - `WG_EMAIL` — your wg-gesucht email
   - `WG_PASSWORD` — your wg-gesucht password
3. The workflow runs twice daily (07:00 & 19:00 UTC). Change the `cron` line in
   [.github/workflows/wg-bot.yml](.github/workflows/wg-bot.yml) to adjust.
   Remember cron is in **UTC**.
4. You can also trigger it manually from the **Actions** tab
   (“Run workflow”).

The workflow commits the updated [state.json](state.json) after each run so the
same listing is never messaged twice.

## Tuning

- `max_listings_per_run` in [config.yaml](config.yaml) — how many of the newest
  listings to contact per run. Keep it small to stay under the radar.
- `dedupe` — set to `false` to ignore the contacted-history (not recommended).
- The `time.sleep(5)` between messages in [wg_gesucht.py](wg_gesucht.py) adds a
  human-like pause; increase it if you hit rate limits.
- `HEADLESS=0` env var runs the browser visibly (for debugging).

## How it works

The bot uses [Playwright](https://playwright.dev/python/) to drive a headless
Chromium browser:

1. Logs in through the website's normal login form.
2. Loads your filtered search page and reads the listing IDs.
3. For each new listing, opens the "Nachricht senden" page, dismisses the
   security-tips dialog, fills in your message (with the recipient's name), and
   clicks **Senden**.
4. Records contacted listing IDs in [state.json](state.json) so none are
   messaged twice.

This mirrors what the maintained community bots
([nickirk/immo](https://github.com/nickirk/immo),
[ale-grassi/wgbot](https://github.com/ale-grassi/wgbot),
[jonasdieker/wg-gesucht-bot](https://github.com/jonasdieker/wg-gesucht-bot)) do,
because WG-Gesucht's send endpoint requires a full browser login session.
