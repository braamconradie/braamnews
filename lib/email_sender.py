"""Render the briefing into HTML and send it over Gmail SMTP with an app password."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

_STYLE = """
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         color: #1a1a1a; background: #f5f5f5; margin: 0; padding: 0; }
  .wrap { max-width: 640px; margin: 0 auto; padding: 24px 16px; }
  .header { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .subheader { color: #666; font-size: 13px; margin-bottom: 24px; }
  .section { background: #fff; border-radius: 8px; padding: 18px 20px; margin-bottom: 16px;
             box-shadow: 0 1px 2px rgba(0,0,0,0.06); }
  .section-title { font-size: 15px; font-weight: 700; color: #0b5d3b; margin-bottom: 10px;
                   text-transform: uppercase; letter-spacing: 0.03em; }
  ul { margin: 0; padding-left: 20px; }
  li { margin-bottom: 8px; line-height: 1.4; font-size: 14px; }
  li a { color: #0b5d3b; text-decoration: none; }
  .market-line { font-size: 14px; margin-bottom: 6px; }
  .market-symbol { font-weight: 700; }
  .up { color: #0a7a2f; }
  .down { color: #b3261e; }
  .empty { color: #888; font-style: italic; font-size: 14px; }
  .reflection { font-size: 14px; line-height: 1.6; font-style: italic; color: #333; }
  .footer { color: #999; font-size: 11px; text-align: center; margin-top: 24px; }
"""


def _fmt_pct(value: float) -> str:
    cls = "up" if value >= 0 else "down"
    sign = "+" if value >= 0 else ""
    return f'<span class="{cls}">{sign}{value:.2f}%</span>'


def _render_bullets(bullets: list[dict]) -> str:
    if not bullets:
        return '<div class="empty">No significant developments today.</div>'
    items = []
    for b in bullets:
        text = escape(b.get("text", ""))
        url = b.get("url")
        source = escape(b.get("source", "")) if b.get("source") else ""
        if url:
            link = f' — <a href="{escape(url)}">{source or "source"}</a>' if source or url else ""
        else:
            link = f" — {source}" if source else ""
        items.append(f"<li>{text}{link}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _render_markets_section(section: dict, data: dict, commentary: dict) -> str:
    lines = []
    for symbol, quote in (data.get("crypto") or {}).items():
        if not quote or quote.get("price") is None:
            continue
        note = escape(commentary.get(symbol, "")) if commentary else ""
        lines.append(
            f'<div class="market-line"><span class="market-symbol">{symbol}</span> '
            f"${quote['price']:,.2f} ({_fmt_pct(quote['change_24h_pct'])}) {note}</div>"
        )
    for symbol, quote in (data.get("stocks") or {}).items():
        if not quote or quote.get("price") is None:
            continue
        note = escape(commentary.get(symbol, "")) if commentary else ""
        lines.append(
            f'<div class="market-line"><span class="market-symbol">{symbol}</span> '
            f"${quote['price']:,.2f} ({_fmt_pct(quote['change_pct'])}) {note}</div>"
        )
    fx = data.get("fx")
    fx_label = data.get("fx_label", "FX")
    min_move = data.get("fx_min_move_pct", 0.5)
    if fx and fx.get("price") is not None and abs(fx["change_pct"]) >= min_move:
        lines.append(
            f'<div class="market-line"><span class="market-symbol">{fx_label}</span> '
            f"{fx['price']:.4f} ({_fmt_pct(fx['change_pct'])})</div>"
        )
    if not lines:
        return '<div class="empty">No market data available today.</div>'
    return "".join(lines)


def render_html(sections_config: list[dict], summary: dict, raw_data: dict, briefing_date: str) -> str:
    body_parts = [f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body><div class="wrap">
<div class="header">Daily Briefing</div>
<div class="subheader">{escape(briefing_date)}</div>
"""]

    summary_sections = summary.get("sections", {})

    for section in sections_config:
        section_id = section["id"]
        section_result = summary_sections.get(section_id, {})
        body_parts.append('<div class="section">')
        body_parts.append(f'<div class="section-title">{escape(section["title"])}</div>')

        if section["type"] == "markets":
            data = raw_data.get(section_id, {})
            commentary = section_result.get("commentary", {})
            body_parts.append(_render_markets_section(section, data, commentary))
        elif section["type"] == "reflection":
            reflection = section_result.get("reflection", "").strip()
            if reflection:
                body_parts.append(f'<div class="reflection">{escape(reflection)}</div>')
            else:
                body_parts.append('<div class="empty">No reflection generated today.</div>')
        else:
            body_parts.append(_render_bullets(section_result.get("bullets", [])))

        body_parts.append("</div>")

    body_parts.append(
        '<div class="footer">Generated automatically by your daily-briefing app.</div>'
    )
    body_parts.append("</div></body></html>")
    return "".join(body_parts)


def send_email(smtp_user: str, smtp_app_password: str, recipient: str, subject: str,
               html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_app_password)
        server.sendmail(smtp_user, [recipient], msg.as_string())

    logger.info("Email sent to %s", recipient)
