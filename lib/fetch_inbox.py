"""Read articles you emailed yourself and tagged with a Gmail label.

You paste an article's text into an email, apply a Gmail label (default
"Briefing"), and this module reads everything under that label over IMAP —
using the same GMAIL_ADDRESS / GMAIL_APP_PASSWORD already configured — and
returns each as a briefing item. After a message is read successfully its
label is removed so it is not included again (the message itself stays in All
Mail; nothing is deleted).

Requires IMAP to be enabled in Gmail (Settings → Forwarding and POP/IMAP →
Enable IMAP). Fails open: any error logs a diagnostic and returns [].
"""

import email
import imaplib
import logging
import os
from email.header import decode_header, make_header

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IMAP_HOST = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
MAX_BODY_CHARS = 6000


def _decode_subject(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or "(no subject)"


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", "replace")


def _get_body(msg) -> str:
    """Return the message body as plain text, preferring text/plain."""
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                text = _part_text(part).strip()
                if text:
                    return text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = _part_text(part)
                if html:
                    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return ""
    text = _part_text(msg)
    if msg.get_content_type() == "text/html" and text:
        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return text.strip()


def fetch_inbox(label: str = "Briefing", max_items: int = 15) -> list[dict]:
    address = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not password:
        logger.warning("Inbox: GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set — skipping.")
        return []

    items: list[dict] = []
    imap = None
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(address, password)

        status, _ = imap.select(f'"{label}"')  # read-write, so we can remove the label
        if status != "OK":
            logger.warning("Inbox: Gmail label %r not found — create it and apply it "
                           "to the emails you want included. Skipping.", label)
            return []

        status, data = imap.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            logger.info("Inbox: no labelled emails to process.")
            return []

        uids = data[0].split()
        logger.info("Inbox: %d labelled email(s) under %r", len(uids), label)

        processed_uids = []
        for uid in uids[:max_items]:
            try:
                st, msgdata = imap.uid("fetch", uid, "(RFC822)")
                if st != "OK" or not msgdata or not msgdata[0]:
                    continue
                msg = email.message_from_bytes(msgdata[0][1])
                subject = _decode_subject(msg.get("Subject", ""))
                body = _get_body(msg)
                if not body:
                    logger.warning("Inbox: empty body for %r — skipping.", subject)
                    continue
                items.append({
                    "title": subject,
                    "body": body[:MAX_BODY_CHARS],
                    "source": "Emailed by you",
                    "link": "",
                })
                processed_uids.append(uid)
            except Exception:
                logger.exception("Inbox: failed to read a message — skipping it.")

        # Remove the label from successfully-read messages so they aren't
        # repeated. In Gmail, expunging from a label folder removes that label
        # and archives the message to All Mail (default IMAP behaviour) — it is
        # not deleted.
        if processed_uids:
            try:
                for uid in processed_uids:
                    imap.uid("store", uid, "+FLAGS", "(\\Deleted)")
                imap.expunge()
                logger.info("Inbox: cleared label from %d processed email(s).",
                            len(processed_uids))
            except Exception:
                logger.exception("Inbox: could not clear the label (items may repeat "
                                 "next run).")

        return items
    except Exception:
        logger.exception("Inbox: IMAP fetch failed — skipping this section.")
        return []
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
