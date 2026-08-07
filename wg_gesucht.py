"""
WG-Gesucht auto-messenger (Playwright edition).

Logs in with your email/password through the real website (no official API or
token exists), finds the newest listings on your filtered search page, and
sends a templated message to each listing owner. Designed to run on a schedule
(e.g. GitHub Actions cron).

Why Playwright and not plain HTTP requests?
-------------------------------------------
WG-Gesucht's message-sending endpoint validates the full browser login session
(cookies set by the website's own login flow), which is impractical to
replicate with bare HTTP calls. Driving a headless browser logs in exactly like
a real user and reliably sends messages. This is the approach used by the
maintained community bots.

Automated access may violate WG-Gesucht's Terms of Service. Use at your own
risk and keep the volume low.

Credentials are read from the environment:
    WG_EMAIL, WG_PASSWORD
(set as GitHub Actions secrets, or in a local .env file).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import yaml
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.wg-gesucht.de"
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_message(message_file: str) -> str:
    return (ROOT / message_file).read_text(encoding="utf-8")


def load_state() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return set()
    return set()


def save_state(contacted: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(contacted)), encoding="utf-8")


def goto(page, url: str, tries: int = 3) -> None:
    """Navigate with retries. WG-Gesucht sometimes aborts the first request."""
    last_err = None
    for attempt in range(tries):
        try:
            page.goto(url, wait_until="commit", timeout=30000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def accept_cookies(page) -> None:
    """Dismiss the consent banner if present (it sits in an iframe or inline)."""
    for sel in (
        "#cmpwelcomebtnyes a",
        "#cmpbntyestxt",
        "#cmpbox button:has-text('Alle akzeptieren')",
        "#cmpbox button:has-text('Akzeptieren')",
        "#cmpbox button:has-text('Accept all')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Accept all')",
    ):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                el.click(timeout=1500, force=True)
                return
        except PWTimeout:
            continue
        except Exception:
            continue


def login(page, email: str, password: str) -> None:
    """Log in through the website UI so all session cookies are set.

    WG-Gesucht now uses a two-step sign-in modal: first enter the email address
    and click "Weiter", then enter the password on the "Willkommen zurück" step
    and submit with "Einloggen".
    """
    goto(page, f"{BASE_URL}/?modal=sign_in")
    accept_cookies(page)

    # Step 1: email address, then "Weiter".
    page.fill("#pre_session_email", email, timeout=20000)
    email_form = page.locator("form:has(#pre_session_email)").first
    if email_form.count():
        email_form.locator("button[type='submit'], input[type='submit']").first.click(force=True)
    else:
        page.get_by_role("button", name="Weiter", exact=True).last.click(force=True)

    # Step 2: password on the "Willkommen zurück" screen, then "Einloggen".
    page.fill("#login_password", password, timeout=20000)
    password_form = page.locator("form:has(#login_password)").first
    if password_form.count():
        password_form.locator("button[type='submit'], input[type='submit']").first.click(force=True)
    else:
        page.get_by_role("button", name="Einloggen", exact=True).last.click(force=True)

    # On success the sign-in modal closes and the password field detaches.
    try:
        page.wait_for_selector("#login_password", state="hidden", timeout=20000)
    except PWTimeout as exc:
        raise RuntimeError(
            "Login did not complete. Check WG_EMAIL/WG_PASSWORD "
            "(an additional verification step may also be required)."
        ) from exc
    print("Logged in.")


def build_page_url(search_url: str, page_index: int) -> str:
    """Set the page number (0-based) in a WG-Gesucht search URL.

    The page number is the last numeric segment before ``.html`` in the path,
    e.g. ``...Muenchen.90.0+1+2.1.0.html`` is page 0, ``...1.1.html`` is page 1.
    Any query string after ``.html`` is preserved.
    """
    path, sep, query = search_url.partition("?")
    new_path = re.sub(r"\.(\d+)\.html$", f".{page_index}.html", path)
    return new_path + sep + query


def fetch_listings(page, search_url: str, max_pages: int = 1) -> list[dict]:
    """Return listings across up to ``max_pages`` search-result pages.

    Stops early once a page yields no new ads (i.e. the end of the results).
    """
    listings: list[dict] = []
    seen: set[str] = set()

    for page_index in range(max_pages):
        url = build_page_url(search_url, page_index)
        goto(page, url)
        accept_cookies(page)
        html = page.content()

        new_on_page = 0
        for ad_id in re.findall(r'data-ad_id="(\d+)"', html):
            if ad_id in seen:
                continue
            seen.add(ad_id)
            href_m = re.search(rf'href="(/[^"]+\.{ad_id}\.html)"', html)
            href = href_m.group(1) if href_m else ""
            listings.append({"ad_id": ad_id, "href": href})
            new_on_page += 1

        print(f"  page {page_index + 1}: {new_on_page} new listing(s)")
        if new_on_page == 0:
            break  # reached the end of the results

    return listings


def send_message(page, listing: dict, body_template: str) -> bool:
    """Open the contact page for a listing and send the message."""
    href = listing.get("href")
    if not href:
        print(f"  ✗ no URL for ad {listing['ad_id']}, skipping")
        return False

    contact_url = f"{BASE_URL}/nachricht-senden{href}"
    goto(page, contact_url)
    accept_cookies(page)

    # Dismiss the "Sicherheitstipps" modal if it appears.
    try:
        btn = page.locator("#sec_advice_submit_button")
        if btn.is_visible(timeout=3000):
            btn.click()
    except Exception:
        pass

    # Resolve the recipient name from the page heading.
    recipient = ""
    try:
        heading = page.locator("h1:has-text('Nachricht senden an')").first
        txt = heading.inner_text(timeout=3000)
        m = re.search(r"Nachricht senden an\s+(.+)", txt)
        if m:
            recipient = m.group(1).strip()
    except Exception:
        pass

    text = body_template.replace("{recipient}", recipient or "zusammen")

    try:
        page.fill("#message_input", text, timeout=10000)
    except PWTimeout:
        print(f"  ✗ no message box for ad {listing['ad_id']} (maybe already contacted)")
        return False

    # Click the visible "Senden" button.
    page.get_by_role("button", name="Senden").first.click()

    # Confirm success. WG-Gesucht changes wording/UI over time, so check several
    # success markers instead of one exact phrase.
    success_selectors = (
        "text=/wurde erfolgreich kontaktiert/i",
        "text=/nachricht (wurde )?(erfolgreich )?(versendet|gesendet)/i",
        "text=/deine nachricht/i",
        ".alert.alert-success",
        ".alert-success",
        ".msgbox_success",
    )
    error_selectors = (
        "text=/zu viele nachrichten/i",
        "text=/spam/i",
        "text=/captcha/i",
        "text=/fehler/i",
    )

    deadline = time.time() + 15
    while time.time() < deadline:
        for sel in success_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=700):
                    print(f"  ✓ messaged ad {listing['ad_id']}" + (f" ({recipient})" if recipient else ""))
                    return True
            except Exception:
                pass

        for sel in error_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=300):
                    print(f"  ✗ send failed for ad {listing['ad_id']} (website rejected message)")
                    return False
            except Exception:
                pass

        time.sleep(0.4)

    print(f"  ✗ no success confirmation for ad {listing['ad_id']}")
    return False


def main() -> int:
    email = os.environ.get("WG_EMAIL")
    password = os.environ.get("WG_PASSWORD")
    if not email or not password:
        print("ERROR: set WG_EMAIL and WG_PASSWORD environment variables.")
        return 1

    config = load_config()
    body = load_message(config["message_file"])
    dedupe = config.get("dedupe", True)
    contacted = load_state() if dedupe else set()
    max_sends = int(config["max_listings_per_run"])
    max_pages = int(config.get("max_pages", 1))
    headless = os.environ.get("HEADLESS", "1") != "0"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        login(page, email, password)
        listings = fetch_listings(page, config["search_url"], max_pages)
        print(f"Found {len(listings)} listing(s) on the search page.")

        sent = 0
        for listing in listings:
            if sent >= max_sends:
                break
            if dedupe and listing["ad_id"] in contacted:
                print(f"  · skip already-contacted ad {listing['ad_id']}")
                continue

            if send_message(page, listing, body):
                contacted.add(listing["ad_id"])
                sent += 1
                save_state(contacted)
                # Be gentle: pause between messages to look less bot-like.
                time.sleep(5)

        browser.close()

    print(f"Done. Sent {sent} new message(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
