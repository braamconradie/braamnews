"""Fetch recent news items via Google News RSS search (no API key required)."""

import logging
from urllib.parse import quote

import feedparser

logger = logging.getLogger(__name__)


def fetch_google_news(query: str, max_items: int = 4, when: str = "2d",
                       region: str = "NZ", lang: str = "en") -> list[dict]:
    """Search Google News RSS for `query`, restricted to the last `when` period."""
    q = quote(f"{query} when:{when}")
    url = f"https://news.google.com/rss/search?q={q}&hl={lang}-{region}&gl={region}&ceid={region}:{lang}"

    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        logger.warning("Feed parse issue for query %r: %s", query, feed.bozo_exception)

    items = []
    for entry in feed.entries[:max_items]:
        source = None
        if "source" in entry and isinstance(entry.source, dict):
            source = entry.source.get("title")
        items.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "source": source,
            "published": entry.get("published", ""),
        })
    return items


def fetch_section_news(queries: list[str], max_items_per_query: int = 3,
                        max_total: int = 6, when: str = "2d") -> list[dict]:
    """Run several queries for one briefing section, dedup by title, cap total items."""
    seen_titles: set[str] = set()
    all_items: list[dict] = []

    for query in queries:
        try:
            items = fetch_google_news(query, max_items=max_items_per_query, when=when)
        except Exception:
            logger.exception("Failed fetching news for query %r", query)
            continue

        for item in items:
            key = item["title"].strip().lower()
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            all_items.append(item)

    return all_items[:max_total]
