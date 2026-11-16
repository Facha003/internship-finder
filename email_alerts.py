"""Job-alert emails from finn.no and LinkedIn, read from the user's own inbox.

Why this exists: both sites forbid automated access to their pages (see CLAUDE.md),
but both will email new matches for a saved search. Reading those emails is the
legitimate way to get their positions into the list.

How it works:
  * Address and IMAP server are in config.json under "epost".
  * The password (a Gmail *app password*) is kept in the macOS Keychain, never in a file.
    `python3 finder.py --connect-email` stores it.
  * The mailbox is opened read-only and only messages from the alert senders are fetched.

STATUS: the parsers below were written from the known layout of these emails and are
covered by tests/test_core.py with constructed samples. They had not been run against
a real inbox when written (2026-11-16). If the status line in the page shows this
source as working but with 0 positions while alerts are arriving, the email layout
has probably changed: save one alert as .eml, add it as a test case, and fix the parser.
"""
import datetime as dt
import email
import email.policy
import email.utils
import imaplib
import json
import re
import subprocess
from html.parser import HTMLParser

KEYCHAIN_SERVICE = "bachlor-finder-imap"
SENDERS = ("linkedin.com", "finn.no")


class NotConfigured(Exception):
    """Raised when the user has not connected an inbox. Not an error."""


# ---------------------------------------------------------------- keychain

def keychain_get(address):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", address, "-w"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def keychain_set(address, password):
    """Store the password. It is sent on stdin so it never appears in the process list."""
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", password):
        raise ValueError("That does not look like an app password (letters and digits only, no spaces).")
    if not re.fullmatch(r"[^\s'\"\\]+@[^\s'\"\\]+", address):
        raise ValueError("That does not look like an email address.")
    cmd = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {address} -w {password}\n"
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError("Could not save to the Keychain: " + (r.stderr.strip() or "unknown error"))


def keychain_delete(address):
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", address],
                   capture_output=True, text=True, timeout=15)


# ---------------------------------------------------------------- parsing

class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._cur = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._cur = [dict(attrs).get("href") or "", []]

    def handle_endtag(self, tag):
        if tag == "a" and self._cur:
            self.out.append((self._cur[0], " ".join(" ".join(self._cur[1]).split())))
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None and data.strip():
            self._cur[1].append(data.strip())


def _parts(msg):
    plain, html_ = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_content_disposition() == "attachment":
            continue
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html_:
            html_ = text
    return plain, html_


_LI_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s]*-)?(\d{6,})")
_FINN_ID = re.compile(r"finn\.no/(?:[\w/.-]*?(?:finnkode=|/ad/|ad\.html\?finnkode=))?(\d{7,})")
_JUNK_TITLE = re.compile(r"^(view job|se stillingen?|se annonsen?|apply|søk|les mer|read more|vis alle|see all jobs?|"
                         r"easy apply|enkel søknad|\d+ (?:new )?jobs?.*)$", re.I)


def parse_linkedin(plain, html_):
    """Each job in a LinkedIn alert is a block: title, company, location, then a view link."""
    jobs = {}
    lines = [l.strip() for l in (plain or "").splitlines()]
    for i, line in enumerate(lines):
        m = _LI_ID.search(line)
        if not m:
            continue
        before = [l for l in lines[max(0, i - 8):i] if l and not _LI_ID.search(l) and not set(l) <= set("-_=*· ")]
        # the link line may itself start with the label ("View job: https://...")
        before = [l for l in before if not _JUNK_TITLE.match(l)]
        if len(before) >= 1 and m.group(1) not in jobs:
            block = before[-3:]
            jobs[m.group(1)] = {"title": block[0], "employer": block[1] if len(block) > 1 else "",
                                "location": block[2] if len(block) > 2 else ""}
    if not jobs and html_:
        p = _Anchors()
        p.feed(html_)
        texts = {}
        for href, text in p.out:
            m = _LI_ID.search(href)
            if m and text and not _JUNK_TITLE.match(text):
                texts.setdefault(m.group(1), []).append(text)
        for jid, ts in texts.items():
            rest = [t for t in ts[1:] if t != ts[0]]
            emp, _, loc = (rest[0] if rest else "").partition(" · ")
            jobs[jid] = {"title": ts[0], "employer": emp, "location": loc}
    return [{"id": "mail:linkedin:" + jid, "source": "LinkedIn alert", "url": f"https://www.linkedin.com/jobs/view/{jid}/",
             **j} for jid, j in jobs.items()]


def parse_finn(plain, html_):
    """finn.no alerts link each ad by its finnkode. The link text is the ad title."""
    jobs = {}
    if html_:
        p = _Anchors()
        p.feed(html_)
        for href, text in p.out:
            m = _FINN_ID.search(href)
            if not m or not text or _JUNK_TITLE.match(text) or len(text) < 6:
                continue
            cur = jobs.get(m.group(1))
            if cur is None or len(text) > len(cur["title"]):
                jobs[m.group(1)] = {"title": text[:160], "employer": "", "location": ""}
    if not jobs:
        lines = [l.strip() for l in (plain or "").splitlines()]
        for i, line in enumerate(lines):
            m = _FINN_ID.search(line)
            before = [l for l in lines[max(0, i - 4):i] if l and "http" not in l]
            if m and before and m.group(1) not in jobs:
                jobs[m.group(1)] = {"title": before[-1][:160], "employer": "", "location": ""}
    return [{"id": "mail:finn:" + fid, "source": "finn.no alert", "url": f"https://www.finn.no/{fid}", **j}
            for fid, j in jobs.items()]


def parse_message(msg):
    """Positions found in one alert email (an email.message.EmailMessage)."""
    sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    plain, html_ = _parts(msg)
    if sender.endswith("linkedin.com"):
        items = parse_linkedin(plain, html_)
    elif sender.endswith("finn.no"):
        items = parse_finn(plain, html_)
    else:
        return []
    try:
        date = email.utils.parsedate_to_datetime(msg.get("Date")).date().isoformat()
    except (TypeError, ValueError):
        date = ""
    for it in items:
        it["published"] = date
    return items


# ---------------------------------------------------------------- inbox

def settings(cfg):
    e = cfg.get("epost") or {}
    address = (e.get("adresse") or "").strip()
    if not address:
        raise NotConfigured("no inbox connected")
    password = keychain_get(address)
    if not password:
        raise NotConfigured("no password stored for " + address)
    return address, password, e.get("imap_server") or "imap.gmail.com", int(e.get("dager_tilbake") or 30)


def login(address, password, server, ssl_ctx):
    box = imaplib.IMAP4_SSL(server, 993, ssl_context=ssl_ctx, timeout=30)
    box.login(address, password)
    return box


def fetch(cfg, cache_dir, ssl_ctx):
    """All positions from alert emails of the last N days. Parsed emails are cached by
    message id, so each email is downloaded once."""
    address, password, server, days = settings(cfg)
    cache_path = cache_dir / "email_alerts.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    since = dt.date.today() - dt.timedelta(days=days)
    box = login(address, password, server, ssl_ctx)
    try:
        folder = '"[Gmail]/All Mail"' if "gmail" in server else "INBOX"
        if box.select(folder, readonly=True)[0] != "OK":
            box.select("INBOX", readonly=True)
        uids = []
        for sender in SENDERS:
            typ, data = box.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", sender)
            if typ == "OK" and data and data[0]:
                uids += data[0].split()
        fresh = {}
        for uid in uids[-200:]:
            key = uid.decode()
            if key in cache:
                fresh[key] = cache[key]
                continue
            typ, data = box.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
            fresh[key] = parse_message(msg)
    finally:
        try:
            box.logout()
        except Exception:
            pass
    cache_path.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
    cutoff, seen, out = since.isoformat(), set(), []
    for items in fresh.values():
        for it in items:
            if it["id"] not in seen and (it.get("published") or cutoff) >= cutoff:
                seen.add(it["id"])
                out.append(dict(it))
    return out
