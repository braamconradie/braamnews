#!/usr/bin/env python3
"""Entry point: fetch news + market data, summarize with Claude, email the briefing."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from lib.email_sender import render_html, send_email
from lib.fetch_energynews import fetch_energynews
from lib.fetch_inbox import fetch_inbox
from lib.fetch_markets import fetch_crypto, fetch_fx, fetch_stocks
from lib.fetch_news import fetch_section_news
from lib.summarize import summarize_briefing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

REPO_ROOT = Path(__file__).parent
DEFAULT_TIMEZONE = "Pacific/Auckland"


def load_config(path: Path) -> list[dict]:
    with open(path) as f:
        config = yaml.safe_load(f)
    return config["sections"]


def gather_raw_data(sections_config: list[dict]) -> dict:
    raw_data = {}

    for section in sections_config:
        section_id = section["id"]
        section_type = section["type"]

        if section_type == "energynews":
            items = fetch_energynews(section.get("keywords", []))
            raw_data[section_id] = {"items": items}
        elif section_type == "inbox":
            items = fetch_inbox(section.get("subject_marker", "bcdailybrief"))
            raw_data[section_id] = {"items": items}
        elif section_type == "markets":
            crypto = fetch_crypto(section.get("crypto", []))
            stocks = fetch_stocks(section.get("stocks", []))
            fx_config = section.get("fx", {})
            fx = fetch_fx(fx_config["pair"]) if fx_config.get("pair") else None
            items = fetch_section_news(section.get("queries", []))
            raw_data[section_id] = {
                "crypto": crypto,
                "stocks": stocks,
                "fx": fx,
                "fx_label": fx_config.get("label", "FX"),
                "fx_min_move_pct": fx_config.get("min_move_pct", 0.5),
                "items": items,
            }
        else:
            items = fetch_section_news(section.get("queries", []))
            raw_data[section_id] = {"items": items}

        logger.info("Fetched section %s", section_id)

    return raw_data


def main() -> int:
    config_path = Path(os.environ.get("TOPICS_CONFIG", REPO_ROOT / "topics.yaml"))
    sections_config = load_config(config_path)

    anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL", gmail_address)

    tz = ZoneInfo(os.environ.get("BRIEFING_TIMEZONE", DEFAULT_TIMEZONE))
    briefing_date = datetime.now(tz).strftime("%A, %d %B %Y")

    logger.info("Gathering raw data for %d sections", len(sections_config))
    raw_data = gather_raw_data(sections_config)

    logger.info("Summarizing with Claude (%s)", model)
    summary = summarize_briefing(sections_config, raw_data, anthropic_api_key, model=model)

    html = render_html(sections_config, summary, raw_data, briefing_date)

    subject = f"Daily Briefing — {briefing_date}"
    send_email(gmail_address, gmail_app_password, recipient, subject, html)

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
