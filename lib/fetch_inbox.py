"""Read articles you emailed in, identified by a subject-line marker.

You email your Gmail (from any account) with a subject that starts with a
marker (default "bcdailybrief") followed by the article name, and paste the
article text in the body. This module searches the inbox over IMAP — using the
existing GMAIL_ADDRESS / GMAIL_APP_PASSWORD — for unread messages whose subject
contains the marker, and returns each as a briefing item. Processed messages
are marked read so they aren't included again (nothing is deleted).

Requires IMAP enabled in Gmail (Settings → Forwarding and POP/IMAP → Enable
IMAP). Fails open: any error logs a diagnostic and returns [].
"""

import email
import imaplib
import logging
import os
import re
from email.header import decode_header, make_header

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IMAP_HOST = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
MAILBOX = os.environ.get("GMAIL_INBOX_MAILBOX", "INBOX")
MAX_BODY_CHARS = 6000


def _decode_subject(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def _strip_marker(subject: str, marker: str) -> str:
    """Return the article title: the subject with the leading marker removed."""
    # Drop everything up to and including the marker, then tidy separators.
    idx = subject.lower().find(marker.lower())
    rest = subject[idx + len(marker):] if idx != -1 else subject
    return rest.lstrip(" :-–—\t").strip() or "(untitled)"


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


def fetch_inbox(subject_marker: str = "bcdailybrief", max_items: int = 15) -> list[dict]:
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

        status, _ = imap.select(f'"{MAILBOX}"')
        if status != "OK":
            logger.warning("Inbox: mailbox %r not found — skipping.", MAILBOX)
            return []

        # Unread messages whose subject contains the marker. IMAP SUBJECT search
        # is a case-insensitive substring match.
        safe_marker = subject_marker.replace('"', "")
        status, data = imap.uid("search", None, f'(UNSEEN SUBJECT "{safe_marker}")')
        if status != "OK" or not data or not data[0]:
            logger.info("Inbox: no unread emails with subject marker %r.", subject_marker)
            return []

        uids = data[0].split()
        logger.info("Inbox: %d email(s) matching subject marker %r", len(uids), subject_marker)

        processed_uids = []
        for uid in uids[:max_items]:
            try:
                st, msgdata = imap.uid("fetch", uid, "(RFC822)")  # marks the message \Seen
                if st != "OK" or not msgdata or not msgdata[0]:
                    continue
                msg = email.message_from_bytes(msgdata[0][1])
                subject = _decode_subject(msg.get("Subject", ""))
                # Guard against IMAP's loose matching — require the marker present.
                if subject_marker.lower() not in subject.lower():
                    continue
                title = _strip_marker(subject, subject_marker)
                body = _get_body(msg).strip()
                if not body:
                    body = title  # fall back to the subject text if the body is empty
                items.append({
                    "title": title,
                    "body": body[:MAX_BODY_CHARS],
                    "source": "Emailed by you",
                    "link": "",
                })
                processed_uids.append(uid)
            except Exception:
                logger.exception("Inbox: failed to read a message — skipping it.")

        # Mark processed messages read so they aren't repeated (fetch already
        # sets \Seen; this is belt-and-braces). Nothing is deleted.
        for uid in processed_uids:
            try:
                imap.uid("store", uid, "+FLAGS", "(\\Seen)")
            except Exception:
                pass
        if processed_uids:
            logger.info("Inbox: processed %d emailed article(s).", len(processed_uids))

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
