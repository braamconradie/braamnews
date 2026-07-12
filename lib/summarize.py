"""Turn raw fetched data into a structured, AI-written briefing via the Claude API."""

import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are writing a concise daily executive briefing email for a New \
Zealand energy-sector executive who tracks energy market regulation, competitor \
gentailers, energy/telco platform vendors, and a small personal watchlist of crypto \
and equities.

Rules:
- Be terse, specific, and skip anything that is not genuinely newsworthy in the \
supplied data from the last day or two.
- If a section's supplied items contain nothing substantive, say so briefly (e.g. \
"No significant developments today.") instead of padding with generic filler.
- Never invent facts, figures, prices, or events that are not present in the \
supplied data. Only summarize what you are given.
- Each bullet must be 1-2 sentences, plain English, no fluff.
- Return ONLY valid JSON matching the requested schema. No markdown code fences, \
no commentary before or after the JSON.
"""


def _format_items(items: list[dict]) -> str:
    if not items:
        return "(no items found)"
    lines = []
    for item in items:
        source = item.get("source") or "unknown source"
        lines.append(f"- \"{item.get('title', '')}\" ({source}) {item.get('link', '')}")
    return "\n".join(lines)


def _format_market_snapshot(market_data: dict) -> str:
    lines = []
    for symbol, data in (market_data.get("crypto") or {}).items():
        if not data or data.get("price") is None:
            continue
        lines.append(f"- {symbol}: ${data['price']:,.2f} ({data['change_24h_pct']:+.2f}% 24h)")
    for symbol, data in (market_data.get("stocks") or {}).items():
        if not data or data.get("price") is None:
            continue
        lines.append(f"- {symbol}: ${data['price']:,.2f} ({data['change_pct']:+.2f}% vs prev close)")
    fx = market_data.get("fx")
    if fx and fx.get("price") is not None:
        lines.append(f"- {market_data.get('fx_label', 'FX pair')}: {fx['price']:.4f} ({fx['change_pct']:+.2f}%)")
    return "\n".join(lines) if lines else "(no market data available)"


def build_prompt(sections_config: list[dict], raw_data: dict) -> str:
    parts = [
        "Here is today's raw research for each briefing section, in priority order. "
        "Write the briefing content for each section as instructed in the JSON schema below.\n"
    ]

    for section in sections_config:
        section_id = section["id"]
        data = raw_data.get(section_id, {})
        parts.append(f"\n=== SECTION: {section_id} ({section['title']}) ===")
        parts.append(f"Focus: {section['focus'].strip()}")

        if section["type"] == "markets":
            parts.append("Market snapshot:")
            parts.append(_format_market_snapshot(data))
            parts.append("Related news items:")
            parts.append(_format_items(data.get("items", [])))
        else:
            parts.append("News items found:")
            parts.append(_format_items(data.get("items", [])))

    schema_desc = {
        "sections": {
            section["id"]: (
                {"commentary": {"<SYMBOL>": "one-line driver string per symbol with news"}}
                if section["type"] == "markets"
                else {"reflection": "2-4 sentence original reflection string"}
                if section["type"] == "reflection"
                else {"bullets": [{"text": "...", "source": "...", "url": "..."}]}
            )
            for section in sections_config
        }
    }

    parts.append("\n\nRespond with JSON matching exactly this shape (fill in real content):")
    parts.append(json.dumps(schema_desc, indent=2))

    return "\n".join(parts)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def summarize_briefing(sections_config: list[dict], raw_data: dict, api_key: str,
                        model: str = DEFAULT_MODEL) -> dict:
    client = Anthropic(api_key=api_key)
    prompt = build_prompt(sections_config, raw_data)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")

    if response.stop_reason == "max_tokens":
        logger.error(
            "Claude response hit the max_tokens limit and was truncated; the JSON "
            "will be incomplete. Raise max_tokens or trim the number of items."
        )

    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        logger.error("Could not parse Claude response as JSON:\n%s", text)
        raise
