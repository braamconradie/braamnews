"""Fetch subscriber articles from EnergyNews (energynews.co.nz) via a real browser.

The site's login is JavaScript-driven (React server actions), so a plain HTTP
POST can't authenticate. This module drives a headless Chromium browser with
Playwright: it fills the login form, lets the site's own JS submit it, then
scrapes recent article headlines from the listing page.

Credentials come from ENERGYNEWS_USERNAME / ENERGYNEWS_PASSWORD (GitHub
secrets). On every run it writes screenshots + the listing HTML to a debug
directory (uploaded as a workflow artifact) so the page can be inspected and
selectors tuned. Any failure logs a diagnostic and returns [] so the rest of
the briefing still sends.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("ENERGYNEWS_BASE_URL", "https://www.energynews.co.nz").rstrip("/")
LOGIN_URL = os.environ.get("ENERGYNEWS_LOGIN_URL", f"{BASE_URL}/user/login")
LISTING_URL = os.environ.get("ENERGYNEWS_LISTING_URL", BASE_URL)
DEBUG_DIR = os.environ.get("ENERGYNEWS_DEBUG_DIR", "debug")
MAX_AGE_DAYS = int(os.environ.get("ENERGYNEWS_MAX_AGE_DAYS", "7"))
NAV_TIMEOUT_MS = 45000

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_USERNAME_SELECTORS = [
    "input[name='name']", "input#edit-name",
    "input[type='email']", "input[name='mail']",
]
_PASSWORD_SELECTORS = ["input[name='pass']", "input#edit-pass", "input[type='password']"]
_SUBMIT_SELECTORS = [
    "input#edit-submit", "#edit-submit",
    "button[type='submit']", "input[type='submit']",
    "button:has-text('Log in')", "button:has-text('Login')",
]

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items()) if m})


def _parse_date(text: str) -> datetime | None:
    if not text:
        return None
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)", text)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text)
    if m and m[2].lower() in _MONTHS:
        try:
            return datetime(int(m[3]), _MONTHS[m[2].lower()], int(m[1]), tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if m:
        try:
            return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _article_date(anchor) -> datetime | None:
    node = anchor
    for _ in range(4):
        if node is None:
            break
        time_tag = node.find("time") if hasattr(node, "find") else None
        if time_tag is not None:
            dt = _parse_date(time_tag.get("datetime", "")) or _parse_date(time_tag.get_text(" "))
            if dt:
                return dt
        dt = _parse_date(node.get_text(" ")) if hasattr(node, "get_text") else None
        if dt:
            return dt
        node = getattr(node, "parent", None)
    return None


def _fill_first(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.fill(value, timeout=8000)
                logger.info("EnergyNews: filled %s", sel)
                return True
        except Exception:
            continue
    return False


def _click_first(page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=8000)
                logger.info("EnergyNews: clicked %s", sel)
                return True
        except Exception:
            continue
    return False


def _submit_login(page) -> bool:
    """Submit the login form. Pressing Enter in the password field is the most
    reliable trigger for JS/React forms; fall back to clicking the button."""
    for sel in _PASSWORD_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.press("Enter")
                logger.info("EnergyNews: submitted via Enter (%s)", sel)
                page.wait_for_timeout(3000)
                if "/user/login" not in page.url:
                    return True
                break
        except Exception:
            continue
    try:
        btn = page.get_by_role("button", name=re.compile("log ?in", re.I)).first
        if btn.count() > 0:
            btn.click(timeout=8000)
            logger.info("EnergyNews: clicked Login button (role)")
            return True
    except Exception:
        pass
    return _click_first(page, _SUBMIT_SELECTORS)


def _looks_logged_in(page) -> bool:
    if "/user/login" in page.url:
        return False
    body = page.content().lower()
    return ("log out" in body) or ("logout" in body) or ("sign out" in body)


def _diagnose_failure(page) -> None:
    """Log *why* login failed so it's visible in the run log (no screenshot needed)."""
    try:
        content = page.content().lower()
    except Exception:
        content = ""
    for marker in ("recaptcha", "hcaptcha", "g-recaptcha", "cloudflare",
                   "verify you are human", "are you human", "captcha"):
        if marker in content:
            logger.warning("EnergyNews: possible CAPTCHA/bot challenge on the page "
                           "(matched %r) — automated login may not be feasible.", marker)
            return
    for sel in (".messages--error", ".messages.error", "[role='alert']",
                ".alert-danger", ".form-item--error-message", ".region-messages"):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                txt = loc.nth(i).inner_text(timeout=2000).strip()
                if txt:
                    logger.warning("EnergyNews: on-page message after submit: %s",
                                   " ".join(txt.split())[:300])
                    return
        except Exception:
            continue
    logger.info("EnergyNews: no explicit error message or CAPTCHA found after submit "
                "(form may have silently rejected the credentials).")


def _extract_articles(html: str, keywords: list[str], max_items: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    logger.info("EnergyNews: listing returned %d anchors", len(anchors))

    lowered = [k.lower() for k in (keywords or [])]
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    keyword_hits: list[dict] = []
    recent: list[dict] = []
    seen: set[str] = set()
    dropped_old = 0
    undated = 0

    for a in anchors:
        title = a.get_text(strip=True)
        href = a["href"]
        if len(title) < 25:
            continue
        url = urljoin(BASE_URL, href)
        if url in seen or "/user/" in url or url.rstrip("/") == BASE_URL:
            continue
        seen.add(url)

        published = _article_date(a)
        if published is not None and published < cutoff:
            dropped_old += 1
            continue
        if published is None:
            undated += 1

        item = {"title": title, "link": url, "source": "EnergyNews"}
        if lowered and any(k in title.lower() for k in lowered):
            keyword_hits.append(item)
        else:
            recent.append(item)

    ordered = keyword_hits + recent
    logger.info(
        "EnergyNews: %d candidates within %dd (%d keyword-matched, %d undated kept, "
        "%d dropped as older)", len(ordered), MAX_AGE_DAYS, len(keyword_hits),
        undated, dropped_old)
    return ordered[:max_items]


def fetch_energynews(keywords: list[str] | None = None, max_items: int = 10) -> list[dict]:
    username = os.environ.get("ENERGYNEWS_USERNAME")
    password = os.environ.get("ENERGYNEWS_PASSWORD")
    if not username or not password:
        logger.warning("EnergyNews: ENERGYNEWS_USERNAME/PASSWORD not set — skipping.")
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.exception("EnergyNews: playwright not installed — skipping.")
        return []

    os.makedirs(DEBUG_DIR, exist_ok=True)

    def _shot(page, name):
        try:
            page.screenshot(path=os.path.join(DEBUG_DIR, name), full_page=True)
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_UA,
                                          viewport={"width": 1366, "height": 900})
            page = context.new_page()
            page.set_default_timeout(NAV_TIMEOUT_MS)

            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            _shot(page, "01_login.png")

            got_user = _fill_first(page, _USERNAME_SELECTORS, username)
            got_pass = _fill_first(page, _PASSWORD_SELECTORS, password)
            if not (got_user and got_pass):
                logger.warning("EnergyNews: could not locate login fields "
                               "(user=%s pass=%s).", got_user, got_pass)

            _submit_login(page)
            try:
                page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
            except Exception:
                pass
            _shot(page, "02_after_login.png")

            if _looks_logged_in(page):
                logger.info("EnergyNews: login succeeded (url=%s).", page.url)
            else:
                logger.warning("EnergyNews: login NOT confirmed — still at %s.", page.url)
                _diagnose_failure(page)

            page.goto(LISTING_URL, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
            _shot(page, "03_listing.png")
            html = page.content()
            try:
                with open(os.path.join(DEBUG_DIR, "listing.html"), "w",
                          encoding="utf-8") as fh:
                    fh.write(html)
            except Exception:
                pass

            context.close()
            browser.close()

        return _extract_articles(html, keywords or [], max_items)
    except Exception:
        logger.exception("EnergyNews: browser fetch failed — skipping this section.")
        return []
