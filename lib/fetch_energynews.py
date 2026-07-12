"""Fetch subscriber articles from EnergyNews (energynews.co.nz), a Drupal site.

Logs in with a normal authenticated requests session (Drupal's standard
`user_login_form`), then pulls recent article headlines from a listing page.
Credentials come from the ENERGYNEWS_USERNAME / ENERGYNEWS_PASSWORD env vars
(GitHub secrets) — never hard-coded.

This module is deliberately defensive: any failure logs a diagnostic and
returns an empty list so the rest of the briefing still sends. The log output
is verbose on purpose so the first real GitHub Actions run tells us exactly
what the site returned and we can tune selectors from there.
"""

import logging
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("ENERGYNEWS_BASE_URL", "https://www.energynews.co.nz").rstrip("/")
LOGIN_URL = os.environ.get("ENERGYNEWS_LOGIN_URL", f"{BASE_URL}/user/login")
LISTING_URL = os.environ.get("ENERGYNEWS_LISTING_URL", BASE_URL)

# Realistic browser UA — the site 403s obvious bot/user-agents.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-NZ,en;q=0.9",
}


def _login(session: requests.Session, username: str, password: str) -> bool:
    """Perform a Drupal form login. Returns True if it looks authenticated."""
    resp = session.get(LOGIN_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form = (
        soup.find("form", id=re.compile("user-login", re.I))
        or soup.find("form", attrs={"action": re.compile("login", re.I)})
        or soup.find("form")
    )
    if form is None:
        logger.error("EnergyNews: no login form found on %s", LOGIN_URL)
        return False

    # Collect every hidden/default input the form ships with (form_build_id,
    # form_id, op, honeypots, etc.), then overwrite the credential fields.
    data: dict[str, str] = {}
    for inp in form.find_all(("input", "button")):
        name = inp.get("name")
        if name:
            data[name] = inp.get("value", "")

    # Drupal's default field names are `name` (username) and `pass` (password).
    data["name"] = username
    data["pass"] = password

    action = form.get("action") or LOGIN_URL
    post_url = urljoin(LOGIN_URL, action)
    logger.info("EnergyNews: submitting login to %s (fields: %s)",
                post_url, ", ".join(sorted(data.keys())))

    post = session.post(post_url, data=data, timeout=30)
    post.raise_for_status()

    # Signs of a successful Drupal login: a logout link, or the login form gone.
    body = post.text.lower()
    logged_in = ("user/logout" in body) or ("log out" in body) or ("sign out" in body)
    if not logged_in:
        # Drupal surfaces bad credentials as an inline error message.
        if "unrecognized username or password" in body or "sorry, unrecognized" in body:
            logger.error("EnergyNews: login rejected — check the ENERGYNEWS_* secrets.")
        else:
            logger.warning(
                "EnergyNews: login status uncertain (no logout link found). "
                "Landed on %s (%d chars).", post.url, len(post.text))
    else:
        logger.info("EnergyNews: login succeeded.")
    return logged_in


def _extract_articles(session: requests.Session, keywords: list[str],
                      max_items: int) -> list[dict]:
    """Pull recent headline links from the listing page; soft-filter by keywords."""
    resp = session.get(LISTING_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    anchors = soup.find_all("a", href=True)
    logger.info("EnergyNews: listing %s returned %d anchors", LISTING_URL, len(anchors))

    lowered_keywords = [k.lower() for k in (keywords or [])]
    keyword_hits: list[dict] = []
    recent: list[dict] = []
    seen: set[str] = set()

    for a in anchors:
        title = a.get_text(strip=True)
        href = a["href"]
        # Headline-ish: reasonably long text, not a nav/footer link.
        if len(title) < 25:
            continue
        url = urljoin(BASE_URL, href)
        if url in seen or "/user/" in url or url.rstrip("/") == BASE_URL:
            continue
        seen.add(url)

        item = {"title": title, "link": url, "source": "EnergyNews"}
        if lowered_keywords and any(k in title.lower() for k in lowered_keywords):
            keyword_hits.append(item)
        else:
            recent.append(item)

    # Keyword matches first (the focus topics), then fill with other recent items
    # so Claude still sees context; Claude filters to the section focus.
    ordered = keyword_hits + recent
    logger.info("EnergyNews: %d headline candidates (%d keyword-matched)",
                len(ordered), len(keyword_hits))
    return ordered[:max_items]


def fetch_energynews(keywords: list[str] | None = None, max_items: int = 10) -> list[dict]:
    """Log in and return recent EnergyNews headlines as briefing items.

    Returns an empty list on any failure (missing creds, login blocked, site
    change) so the briefing still sends without this section.
    """
    username = os.environ.get("ENERGYNEWS_USERNAME")
    password = os.environ.get("ENERGYNEWS_PASSWORD")
    if not username or not password:
        logger.warning("EnergyNews: ENERGYNEWS_USERNAME/PASSWORD not set — skipping.")
        return []

    session = requests.Session()
    session.headers.update(_HEADERS)

    try:
        if not _login(session, username, password):
            logger.warning("EnergyNews: proceeding without confirmed login; "
                           "results may be limited to public content.")
        return _extract_articles(session, keywords or [], max_items)
    except Exception:
        logger.exception("EnergyNews: fetch failed — skipping this section.")
        return []
