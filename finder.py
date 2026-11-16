#!/usr/bin/env python3
"""Find organisations around Oslo that could host a bachelor project.

Pipeline: company registry (brreg.no) + seeds.json -> read each website ->
keyword score -> results.html and results.csv.

Usage:
    python3 finder.py              full run
    python3 finder.py --limit 30   quick test (30 registry companies + seeds)
    python3 finder.py --refresh    ignore the cache and fetch everything again
"""
import argparse
import csv
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

import listings

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
PAGES = CACHE / "pages"
BRREG_API = "https://data.brreg.no/enhetsregisteret/api/enheter"
BRREG_LOOKUP = "https://virksomhet.brreg.no/nb/oppslag/enheter/"
UA = "BachelorHostFinder/1.0 (student project; python-urllib)"
MAX_BYTES = 1_500_000
MAX_TEXT = 200_000
REGISTRY_MAX_AGE = 7 * 24 * 3600
CACHE_DAYS = 7  # web pages older than this are fetched again (config: crawl.cache_days)

SSL_CTX = ssl.create_default_context()
if os.path.exists("/etc/ssl/cert.pem"):  # python.org builds on macOS ship without CA certs
    SSL_CTX.load_verify_locations("/etc/ssl/cert.pem")


# ---------------------------------------------------------------- fetching

def http_get(url, timeout, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", max_bytes=MAX_BYTES,
             headers=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "nb,no;q=0.9,en;q=0.7",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        data = r.read(max_bytes)
        charset = r.headers.get_content_charset() or "utf-8"
        try:
            body = data.decode(charset, errors="replace")
        except LookupError:
            body = data.decode("utf-8", errors="replace")
        return r.geturl(), r.headers.get_content_type(), body


class PageParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self.desc = [], [], ""
        self._skip, self._a = 0, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._a = [href, []]
        elif tag == "meta":
            a = dict(attrs)
            key = (a.get("name") or a.get("property") or "").lower()
            content = (a.get("content") or "").strip()
            if key in ("description", "og:description", "keywords") and content:
                self.text.append(content)
                if key != "keywords" and len(content) > len(self.desc):
                    self.desc = content

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._a:
            self.links.append([self._a[0], " ".join(self._a[1])[:120]])
            self._a = None

    def handle_data(self, data):
        if self._skip:
            return
        data = data.strip()
        if data:
            self.text.append(data)
            if self._a:
                self._a[1].append(data)


def cache_path(kind, url):
    return PAGES / f"{kind}-{hashlib.sha1(url.encode()).hexdigest()}.json"


def load_page(url, timeout, refresh, kind="page", max_age_days=None):
    """Fetch one URL (cached). Returns dict with ok, final_url, text, links, error."""
    max_age_days = CACHE_DAYS if max_age_days is None else max_age_days
    path = cache_path(kind, url)
    if not refresh and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            fresh = time.time() - path.stat().st_mtime < max_age_days * 86400
            if "description" in cached and fresh:  # stale or old-format entries are fetched again
                return cached
        except (OSError, ValueError):
            pass
    out = {"url": url, "ok": False, "final_url": url, "text": "", "links": [], "description": "", "error": ""}
    try:
        final_url, ctype, body = http_get(url, timeout)
        out["final_url"] = final_url
        if kind == "robots":
            out["ok"] = True
            out["text"] = body[:100_000] if ctype.startswith("text/") else ""
        elif "html" not in ctype:
            out["error"] = f"not html ({ctype})"
        else:
            p = PageParser()
            try:
                p.feed(body)
            except Exception as e:  # malformed markup: keep what we got
                out["error"] = f"parse: {e}"
            out["ok"] = True
            out["text"] = " ".join(p.text)[:MAX_TEXT]
            out["links"] = p.links[:600]
            out["description"] = " ".join(p.desc.split())[:400]
    except urllib.error.HTTPError as e:
        out["error"] = f"HTTP {e.code}"
    except Exception as e:
        out["error"] = type(e).__name__ + (f": {e}" if str(e) else "")
        out["error"] = out["error"][:160]
    try:
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def robots_for(origin, timeout, refresh):
    rp = urllib.robotparser.RobotFileParser()
    res = load_page(origin + "/robots.txt", timeout, refresh, kind="robots")
    rp.parse(res["text"].splitlines() if res["ok"] else [])
    return rp


# ---------------------------------------------------------------- registry

def pretty_name(name):
    words = []
    for w in name.split():
        words.append(w.capitalize() if len(w) > 3 and w.isupper() and w.isalpha() else w)
    return " ".join(words)


def fetch_registry(cfg, refresh):
    path = CACHE / "registry.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < REGISTRY_MAX_AGE:
        return json.loads(path.read_text(encoding="utf-8"))
    reg = cfg["registry"]
    found = {}
    for code in reg["naeringskoder"]:
        for knr, kname in reg["kommuner"].items():
            page, total_pages, n = 0, 1, 0
            while page < total_pages:
                q = urllib.parse.urlencode({
                    "naeringskode": code, "kommunenummer": knr,
                    "fraAntallAnsatte": reg["min_ansatte"], "size": 100, "page": page,
                })
                _, _, body = http_get(f"{BRREG_API}?{q}", 30, accept="application/json", max_bytes=20_000_000)
                d = json.loads(body)
                total_pages = d.get("page", {}).get("totalPages", 0)
                for e in d.get("_embedded", {}).get("enheter", []):
                    if e.get("konkurs") or e.get("underAvvikling") or e.get("underTvangsavviklingEllerTvangsopplosning"):
                        continue
                    n += 1
                    found[e["organisasjonsnummer"]] = {
                        "id": e["organisasjonsnummer"],
                        "orgnr": e["organisasjonsnummer"],
                        "name": pretty_name(e.get("navn", "")),
                        "source": "registry",
                        "employees": e.get("antallAnsatte"),
                        "industry": (e.get("naeringskode1") or {}).get("beskrivelse", ""),
                        "municipality": kname,
                        "website": e.get("hjemmeside") or "",
                        "reg_email": e.get("epostadresse") or "",
                        "note": "",
                        "past_client": False,
                    }
                page += 1
            print(f"  registry {code} {kname}: {n}")
    out = list(found.values())
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def load_seeds():
    path = ROOT / "seeds.json"
    if not path.exists():
        return []
    out = []
    for s in json.loads(path.read_text(encoding="utf-8")):
        out.append({
            "id": "seed:" + site_key(s.get("hjemmeside", s["navn"])),
            "orgnr": "", "name": s["navn"], "source": "seed", "employees": None,
            "industry": "", "municipality": "", "website": s.get("hjemmeside", ""),
            "reg_email": "", "note": s.get("merknad", ""),
            "past_client": bool(s.get("tidligere_oppdragsgiver")),
        })
    return out


FREE_MAIL = {"gmail.com", "hotmail.com", "outlook.com", "online.no", "yahoo.com", "yahoo.no", "live.no",
             "icloud.com", "me.com", "msn.com", "live.com", "hotmail.no"}
NAME_NOISE = {"as", "asa", "norge", "norway", "no", "it"}


def name_key(name):
    words = re.findall(r"\w+", name.lower().split("/")[0].split("(")[0])
    return " ".join(w for w in words if w not in NAME_NOISE)


def merge(seeds, registry):
    """One row per organisation: registry entries that share a website (subsidiaries) or
    match a seed by website/name are folded into a single row. `registry` is sorted by
    employee count, so the largest entity supplies the registry details."""
    by_site, by_name, out = {}, {}, []
    for s in seeds:
        out.append(s)
        if s["website"]:
            by_site[site_key(s["website"])] = s
        by_name[name_key(s["name"])] = s
    for c in registry:
        if not c["website"] and c["reg_email"]:  # no website registered: try the email's domain
            dom = c["reg_email"].rpartition("@")[2].lower()
            if dom and dom not in FREE_MAIL:
                c["website"] = dom
        key = site_key(c["website"]) if c["website"] else ""
        target = (by_site.get(key) if key else None) or by_name.get(name_key(c["name"]))
        if target is None:
            out.append(c)
            if key:
                by_site[key] = c
            by_name.setdefault(name_key(c["name"]), c)
            continue
        if not target["orgnr"]:  # seed without registry details yet
            for f in ("orgnr", "employees", "industry", "municipality", "reg_email"):
                target[f] = c[f]
            target["website"] = target["website"] or c["website"]
        else:
            target["related"] = target.get("related", 0) + 1
    for c in out:
        if c.get("related"):
            extra = f"+{c['related']} related registered entit{'ies' if c['related'] > 1 else 'y'} on the same website."
            c["note"] = (c["note"] + " " + extra).strip()
    return out


# ---------------------------------------------------------------- crawling

def normalise_site(raw):
    raw = (raw or "").strip()
    if not raw or " " in raw or "." not in raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    u = urllib.parse.urlsplit(raw)
    if not u.netloc:
        return ""
    return urllib.parse.urlunsplit(("https", u.netloc.lower(), u.path or "/", "", ""))


def site_key(raw):
    """Domain without www, used to compare/dedupe sites."""
    site = normalise_site(raw)
    host = urllib.parse.urlsplit(site).netloc if site else raw.lower()
    return host[4:] if host.startswith("www.") else host


SUBPAGE_PATTERNS = [  # (priority, regex on href + link text); lower = fetched first
    (0, re.compile(r"student|graduate|trainee|sommerjobb|internship|bachelor|masteroppgave", re.I)),
    (1, re.compile(r"karriere|jobb|career|join|ledige[- ]stilling|work[- ]with[- ]us", re.I)),
    (2, re.compile(r"tjenester|services|kompetanse|teknologi|technology|hva[- ]vi[- ]gj|what[- ]we[- ]do|l[oø]sninger|solutions", re.I)),
    (3, re.compile(r"om[- ]oss|about|kontakt|contact", re.I)),
]
CAREER_RE = re.compile(r"student|graduate|trainee|internship|bachelor|masteroppgave|karriere|jobb|career|join|stilling", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|sentry|wixpress|example\.|@\dx\.|noreply|no-reply", re.I)
GOOD_LOCAL = re.compile(r"jobb|karriere|career|rekrutt|recruit|student|hr|people|post|kontakt|contact|hello|hei|info|firmapost", re.I)


def pick_subpages(page, max_n):
    base = page["final_url"]
    base_key = site_key(base)
    seen, cands = {urllib.parse.urldefrag(base)[0].rstrip("/")}, []
    for href, text in page["links"]:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            url = urllib.parse.urldefrag(urllib.parse.urljoin(base, href))[0]
            u = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        if u.scheme not in ("http", "https") or site_key(url) != base_key:
            continue
        if re.search(r"\.(pdf|jpe?g|png|zip|docx?|xlsx?|mp4)$", u.path, re.I):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        probe = u.path + " " + text
        for prio, rx in SUBPAGE_PATTERNS:
            if rx.search(probe):
                seen.add(key)
                cands.append((prio, len(u.path), url))
                break
    cands.sort()
    return [c[2] for c in cands[:max_n]]


def pick_email(company, pages):
    cands = []
    if company["reg_email"]:
        cands.append(company["reg_email"])
    for p in pages:
        for href, _ in p["links"]:
            if href.lower().startswith("mailto:"):
                cands.append(urllib.parse.unquote(href[7:].split("?")[0]))
        cands.extend(EMAIL_RE.findall(p["text"]))
    domain = site_key(company["website"]).split("/")[0]
    reg = company["reg_email"].strip().lower()
    best, best_rank = "", None
    for i, e in enumerate(cands):
        e = e.strip().strip(".").lower()
        if not EMAIL_RE.fullmatch(e) or BAD_EMAIL.search(e):
            continue
        local, _, edom = e.partition("@")
        # Only shared mailboxes (post@, jobb@, ...) or the address the company registered
        # itself. Named employees' addresses found on web pages are left alone.
        if e != reg and not GOOD_LOCAL.search(local):
            continue
        rank = (
            0 if domain and (edom == domain or domain.endswith("." + edom) or edom.endswith("." + domain)) else 1,
            0 if re.search(r"jobb|karriere|career|rekrutt|recruit|student", local) else 1,
            i,
        )
        if best_rank is None or rank < best_rank:
            best, best_rank = e, rank
    return best


SLUG_NOISE = NAME_NOISE - {"it"} | {"nordic", "nordics", "holding", "oslo", "limited", "ltd", "ab", "nuf", "filial", "branch"}


def guess_site(company, timeout, refresh):
    """The registry has no website for about half the companies. Try <name>.no, then
    <name>.com, and accept only if the page mentions the company's name (and, for .com,
    Norway). The result is flagged as a guess in the output."""
    words = [w for w in re.findall(r"\w+", company["name"].lower()) if w not in SLUG_NOISE]
    slug = "".join(words).replace("ø", "o").replace("å", "a").replace("æ", "ae")
    if len(slug) < 4 or not re.fullmatch(r"[a-z0-9]+", slug):
        return ""
    for tld in ("no", "com"):
        page = load_page(f"https://www.{slug}.{tld}/", timeout, refresh)
        if not page["ok"]:
            continue
        text = page["text"].lower()
        if words[0] not in text:
            continue
        if tld == "com" and not re.search(r"norge|norway|oslo|norsk", text):
            continue
        return page["final_url"]
    return ""


def crawl(company, cfg, refresh):
    c = cfg["crawl"]
    site = normalise_site(company["website"])
    company.update({"website": site, "pages": [], "career_url": "", "crawl_status": ""})
    if not site:
        site = guess_site(company, c["timeout_seconds"], refresh)
        if not site:
            company["crawl_status"] = "no website"
            company["email"] = company["reg_email"]
            return company
        company["website"] = site
        company["note"] = (company["note"] + " Website guessed from the company name; check that it is the right one.").strip()
    u = urllib.parse.urlsplit(site)
    tries = [site]
    if not u.netloc.startswith("www."):
        tries.append(urllib.parse.urlunsplit(("https", "www." + u.netloc, u.path, "", "")))
    tries.append(urllib.parse.urlunsplit(("http", u.netloc, u.path, "", "")))
    home = None
    for t in tries:
        home = load_page(t, c["timeout_seconds"], refresh)
        if home["ok"]:
            break
    if not home["ok"]:
        company["crawl_status"] = "could not read website (" + (home["error"] or "unknown") + ")"
        company["email"] = company["reg_email"]
        return company
    fu = urllib.parse.urlsplit(home["final_url"])
    robots = robots_for(f"{fu.scheme}://{fu.netloc}", c["timeout_seconds"], refresh)
    if not robots.can_fetch(UA, home["final_url"]):
        company["crawl_status"] = "website disallows crawlers (robots.txt)"
        company["email"] = company["reg_email"]
        return company
    pages, careers = [home], []
    for url in pick_subpages(home, c["max_subpages"]):
        if not robots.can_fetch(UA, url):
            continue
        # Careers pages are where new postings appear, so they are re-read more often.
        is_career = bool(CAREER_RE.search(urllib.parse.urlsplit(url).path))
        p = load_page(url, c["timeout_seconds"], refresh,
                      max_age_days=c.get("career_cache_days", 1) if is_career else None)
        if p["ok"]:
            pages.append(p)
            path = urllib.parse.urlsplit(url).path
            p["career"] = bool(CAREER_RE.search(path))
            if p["career"]:
                careers.append((len(path), p["final_url"]))
    if careers:  # shortest path = the section front page rather than a single article
        company["career_url"] = min(careers)[1]
    company["pages"] = pages
    company["crawl_status"] = f"read {len(pages)} page{'s' if len(pages) != 1 else ''}"
    company["email"] = pick_email(company, pages)
    return company




# ---------------------------------------------------------------- analysis

TIERS = {"thesis": "Offers thesis projects", "students": "Takes students",
         "none": "No student signal", "unknown": "Unknown"}
TIER_ORDER = {t: i for i, t in enumerate(TIERS)}


def kw_regex(kw):
    """Whole-word match. Trailing * = any ending. Leading = or short ALL-CAPS = case-sensitive."""
    exact = kw.startswith("=")
    core = kw.lstrip("=").rstrip("*")
    pat = r"(?<!\w)" + re.escape(core).replace(r"\ ", r"\s+") + (r"\w*" if kw.endswith("*") else "") + r"(?!\w)"
    case_sensitive = exact or (core.isupper() and len(core) <= 4)
    return re.compile(pat, 0 if case_sensitive else re.I)


def sentence_around(text, start, end):
    """The sentence containing text[start:end], and whether it looks like real prose
    (as opposed to a run of menu items)."""
    lefts = [text.rfind(sep, max(0, start - 260), start) for sep in (". ", "! ", "? ")]
    rights = [i for i in (text.find(sep, end, end + 260) for sep in (". ", "! ", "? ")) if i != -1]
    lfound, rfound = max(lefts) != -1, bool(rights)
    left = max(lefts) + 2 if lfound else max(0, start - 90)
    right = min(rights) + 1 if rfound else min(len(text), end + 90)
    s = " ".join(text[left:right].split())
    words = s.split()
    lower = sum(1 for w in words if w[:1].islower()) / max(1, len(words))
    prose = lfound and rfound and lower > 0.6 and 40 <= len(s) <= 420
    if not prose:
        s = ("" if left == 0 else "… ") + s + ("" if right >= len(text) else " …")
    return s, prose


def find_quote(pages, regexes):
    """Best sentence on the site that mentions one of the keywords. Careers pages and
    real sentences win over front pages and menus."""
    best, best_rank = None, None
    for pi, p in enumerate(pages):
        for label, rx in regexes:
            for n, m in enumerate(rx.finditer(p["text"])):
                if n >= 4:
                    break
                s, prose = sentence_around(p["text"], m.start(), m.end())
                rank = (0 if prose else 1, 0 if p.get("career") else 1, pi, m.start())
                if best_rank is None or rank < best_rank:
                    best, best_rank = {"text": s, "url": p["final_url"], "prose": prose, "keyword": label}, rank
    return best


def build_analyser(cfg, insights):
    sc = cfg["scoring"]
    comp = lambda kws: [(kw.lstrip("=").rstrip("*"), kw_regex(kw)) for kw in kws]
    groups = {g: comp(spec["keywords"]) for g, spec in sc["groups"].items()}
    intern_kws = comp(cfg["internship_keywords"])
    tags = {kind: {label: comp(kws) for label, kws in cfg["tags"][kind].items()} for kind in ("tech", "sectors")}

    def ranked_tags(kind, text, min_count, top):
        counts = []
        for label, kws in tags[kind].items():
            n = sum(len(rx.findall(text)) for _, rx in kws)
            if n >= min_count:
                counts.append((-n, label))
        return [label for _, label in sorted(counts)[:top]]

    def analyse(c):
        pages = c.get("pages", [])
        all_text = " ".join(p["text"] for p in pages)
        # A thesis mention only counts where the company talks about itself as an employer.
        employer_pages = [p for i, p in enumerate(pages) if i == 0 or p.get("career")]
        employer_text = " ".join(p["text"] for p in employer_pages)
        career_text = " ".join(p["text"] for p in pages[1:] if p.get("career"))

        total, matches = 0, {}
        for g, kws in groups.items():
            text = employer_text if g == "thesis" else all_text
            matches[g] = [label for label, rx in kws if rx.search(text)]
            total += sc["groups"][g]["weight"] * min(len(matches[g]), sc["groups"][g]["cap"])
        sb, emp = sc["size_bonus"], c.get("employees")
        if emp and sb["min_ansatte"] <= emp <= sb["max_ansatte"]:
            total += sb["points"]
        if c.get("past_client"):
            total += sc["past_oslomet_client_bonus"]

        if not pages:
            tier = "unknown"
        elif matches["thesis"]:
            tier = "thesis"
        elif len(matches["student"]) >= 2 or any(rx.search(career_text) for _, rx in groups["student"]):
            tier = "students"
        else:
            tier = "none"

        quote = None
        if tier == "thesis":
            quote = find_quote(employer_pages, groups["thesis"])
        elif tier == "students":
            quote = find_quote(pages, groups["student"])

        tech = ranked_tags("tech", all_text, 1, 8)
        # Sectors: counted outside careers pages (which mention schools and universities a lot),
        # and not repeated when the same label is already a tech tag.
        business_text = " ".join(p["text"] for p in pages if not p.get("career"))
        sectors = [x for x in ranked_tags("sectors", business_text, 3, 4) if x not in tech][:3]
        has_ai, has_cloud = bool(matches["ai"]), bool(matches["cloud"])
        idea = {"en": "", "no": "", "source": "generic"}
        for rule in cfg["ideas"]:
            if rule.get("sector") and rule["sector"] not in sectors[:2]:
                continue
            if (rule.get("ai") and not has_ai) or (rule.get("cloud") and not has_cloud):
                continue
            idea.update(en=rule["en"], no=rule["no"])
            break
        description = next((p["description"] for p in pages if p.get("description")), "")
        # insights.json: hand-written notes that override what the keyword pass found.
        note = insights.get(c["id"])
        c["skip"] = False
        if note:
            description = note.get("does") or description
            if note.get("idea"):
                idea = {"en": note["idea"], "no": note.get("idea_no", ""), "source": "tailored"}
            if note.get("tier") in TIERS and pages:
                tier = note["tier"]
            if note.get("hide_quote"):
                quote = None
            c["skip"] = bool(note.get("skip"))
        # Internships / summer jobs: a separate signal with its own quote and link.
        internship = None
        intern_hits = [label for label, rx in intern_kws if rx.search(all_text)]
        if intern_hits:
            q = find_quote(pages, intern_kws)
            if q and not q["prose"]:      # menu or heading text: keep the link, drop the "quote"
                q = {"text": "", "url": q["url"], "prose": False, "keyword": q["keyword"]}
            intern_text = " ".join(p["text"] for p in pages if any(rx.search(p["text"]) for _, rx in intern_kws))
            # Judge eligibility only on real sentences about internships, not on menus or
            # employee bios elsewhere on the site; a bachelor mention anywhere overrides "master only".
            sents = []
            for p in pages:
                for _, rx in intern_kws:
                    for n, m in enumerate(rx.finditer(p["text"])):
                        if n >= 4:
                            break
                        sent, prose = sentence_around(p["text"], m.start(), m.end())
                        if prose and sent not in sents:
                            sents.append(sent)
            about = " ".join(sents)
            internship = {"keywords": intern_hits, "quote": q,
                          "deadline": listings.deadline_in_text(intern_text),
                          "season": listings.season_in_text(intern_text),
                          "fit": listings.eligible(cfg.get("profil", {}), "", about, "summer")}
            if internship["fit"]["verdict"] == "no" and re.search(r"bachelor", intern_text, re.I):
                internship["fit"] = {"verdict": "ok", "reason": "bachelor students mentioned on their site",
                                     "field": internship["fit"]["field"]}
        for q in (quote, internship and internship["quote"]):
            if q and len(q["text"]) > 340:
                q["text"] = q["text"][:330].rsplit(" ", 1)[0] + " …"

        c.update(score=total, matches=matches, tier=tier, quote=quote, tech=tech, sectors=sectors,
                 idea=idea, description=description[:300],
                 internship=internship)
        return c

    return analyse


# ---------------------------------------------------------------- output

def parse_template(name="email_template.txt"):
    path = ROOT / name
    if not path.exists():   # e.g. the bachelor template after that part has been retired
        return {"subject": "", "body": ""}
    raw = path.read_text(encoding="utf-8")
    subject, body = "", raw
    first, _, rest = raw.partition("\n")
    if first.lower().startswith("emne:"):
        subject, body = first[5:].strip(), rest.lstrip("\n")
    return {"subject": subject, "body": body.rstrip() + "\n"}


def public_row(c):
    return {
        "id": c["id"], "name": c["name"], "source": c["source"], "orgnr": c["orgnr"],
        "brreg": BRREG_LOOKUP + c["orgnr"] if c["orgnr"] else "",
        "employees": c["employees"], "industry": c["industry"], "municipality": c["municipality"],
        "website": c["website"], "career_url": c["career_url"], "email": c.get("email", ""),
        "score": c["score"], "matches": c["matches"], "note": c["note"],
        "past_client": c["past_client"], "crawl_status": c["crawl_status"],
        "tier": c["tier"], "best": c["best"], "description": c["description"],
        "tech": c["tech"], "sectors": c["sectors"], "quote": c["quote"], "idea": c["idea"],
        "internship": c["internship"], "job_board": c.get("job_board", ""),
        "openings": c.get("openings", 0),
    }


def write_outputs(rows, cfg, openings, type_labels, status):
    bachelor = cfg.get("bachelor", True)
    if bachelor:
        write_bachelor_csv(rows)
    with open(ROOT / "internships.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["deadline", "deadline_note", "type", "would_they_take_me", "title", "employer", "location",
                    "apply_link", "source", "published", "first_seen"])
        for l in openings:
            d = l["deadline"] or {}
            w.writerow([d.get("date", ""), d.get("text", ""), type_labels.get(l["type"], l["type"]),
                        l["fit"]["verdict"] + ": " + l["fit"]["reason"], l["title"],
                        l["employer"], l["location"], l["url"], l["source"], l["published"], l["first_seen"]])
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "generated_ts": int(time.time()),
        "bachelor": bachelor,
        "status": status,
        "profile": {k: v for k, v in cfg.get("profil", {}).items() if not k.startswith("_")},
        "next_summer": listings.next_summer(),
        "listings": openings,
        "types": type_labels,
        "tiers": TIERS,
        "sectors": list(cfg["tags"]["sectors"]),
        "group": cfg["gruppe"],
        "template": parse_template(),
        "intern_template": parse_template("internship_template.txt"),
        "rows": rows,
    }
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    bookmarklet = json.dumps((ROOT / "save_job_bookmarklet.js").read_text(encoding="utf-8")).replace("</", "<\\/")
    page = tpl.replace('/*__BOOKMARKLET__*/""', bookmarklet).replace("/*__DATA__*/null", data)
    (ROOT / "results.html").write_text(page, encoding="utf-8")


def write_bachelor_csv(rows):
    with open(ROOT / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["best_bet", "availability", "score", "name", "what_they_do", "works_with", "sectors",
                    "their_own_words", "quote_source", "project_idea", "idea_type", "internship_signal", "internship_source", "email", "careers_page",
                    "website", "employees", "municipality", "source", "past_oslomet_client", "note", "crawl", "orgnr"])
        for r in rows:
            q = r["quote"] or {}
            w.writerow(["yes" if r["best"] else "", TIERS[r["tier"]], r["score"], r["name"], r["description"],
                        ", ".join(r["tech"]), ", ".join(r["sectors"]), q.get("text", ""), q.get("url", ""),
                        r["idea"]["en"], r["idea"]["source"],
                        ", ".join((r["internship"] or {}).get("keywords", [])),
                        ((r["internship"] or {}).get("quote") or {}).get("url", ""), r["email"], r["career_url"], r["website"],
                        r["employees"] or "", r["municipality"], r["source"],
                        "yes" if r["past_client"] else "", r["note"], r["crawl_status"], r["orgnr"]])


def print_digest(companies, n):
    """Raw material for writing insights.json by hand."""
    for c in [c for c in companies if c["best"]][:n]:
        q = c["quote"] or {}
        print(f"\n=== {c['id']} | {c['name']} | {TIERS[c['tier']]} | {c['employees']} empl")
        print("desc:", c["description"])
        print("tech:", ", ".join(c["tech"]), "| sectors:", ", ".join(c["sectors"]))
        print("quote:", q.get("text", "")[:300])
        if c.get("pages"):
            print("home:", c["pages"][0]["text"][:700])


def notify(title, body):
    """macOS notification. Text is passed as arguments, never as script."""
    script = ["-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
              "-e", "end run"]
    try:
        subprocess.run(["osascript", *script, body[:230], title], timeout=15, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only use the first N registry companies (quick test)")
    ap.add_argument("--refresh", action="store_true", help="ignore cached registry data and web pages")
    ap.add_argument("--no-open", action="store_true", help="do not open the results page when done")
    ap.add_argument("--no-listings", action="store_true", help="skip fetching job openings (faster)")
    ap.add_argument("--background", action="store_true",
                    help="for the scheduled run: no browser, and a notification when new positions appear")
    ap.add_argument("--digest", type=int, metavar="N", help="print website excerpts for the top N best bets")
    ap.add_argument("--setup", action="store_true", help="answer a few questions to set your profile")
    ap.add_argument("--doctor", action="store_true", help="check that everything works and say how to fix what does not")
    ap.add_argument("--connect-email", action="store_true", help="connect an inbox for finn.no / LinkedIn job alerts")
    ap.add_argument("--disconnect-email", action="store_true", help="forget the connected inbox")
    ap.add_argument("--retire-bachelor", action="store_true", help="switch to internships only (reversible)")
    ap.add_argument("--restore-bachelor", action="store_true", help="switch the bachelor project part back on")
    args = ap.parse_args()

    import tools  # maintenance commands live in tools.py
    env = {"root": ROOT, "cache": CACHE, "ssl_ctx": SSL_CTX, "http_get": http_get}
    for flag, fn in (("setup", tools.setup), ("doctor", tools.doctor), ("connect_email", tools.connect_email),
                     ("disconnect_email", tools.disconnect_email), ("retire_bachelor", tools.retire_bachelor),
                     ("restore_bachelor", tools.restore_bachelor)):
        if getattr(args, flag):
            return fn(env)

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    global CACHE_DAYS
    CACHE_DAYS = cfg["crawl"].get("cache_days", CACHE_DAYS)
    insights_path = ROOT / "insights.json"
    insights = json.loads(insights_path.read_text(encoding="utf-8")) if insights_path.exists() else {}
    PAGES.mkdir(parents=True, exist_ok=True)

    print("1/4 Company registry")
    registry = fetch_registry(cfg, args.refresh)
    registry.sort(key=lambda c: -(c["employees"] or 0))
    if args.limit is not None:
        registry = registry[:args.limit]
    seeds = load_seeds()
    companies = merge(seeds, registry)
    print(f"  {len(registry)} from registry, {len(seeds)} seeds, {len(companies)} after merging duplicates")

    print("2/4 Reading websites (cached after the first run)")
    done, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=cfg["crawl"]["workers"]) as pool:
        futures = [pool.submit(crawl, c, cfg, args.refresh) for c in companies]
        for fut in as_completed(futures):
            fut.result()
            done += 1
            if done % 50 == 0 or done == len(companies):
                print(f"  {done}/{len(companies)}  ({time.time() - t0:.0f}s)")

    for c in companies:
        c["job_board"], c["board_origins"] = listings.job_board_links(c)

    print("3/4 Open positions for students")
    openings, status, type_labels = [], [], {k: v["label"] for k, v in cfg["listings"]["types"].items()}
    if not args.no_listings:
        def fetch_page(url):  # postings change often: cache for a day, and respect robots.txt
            u = urllib.parse.urlsplit(url)
            timeout = cfg["crawl"]["timeout_seconds"]
            if not robots_for(f"{u.scheme}://{u.netloc}", timeout, args.refresh).can_fetch(UA, url):
                return None
            path = cache_path("page", url)
            stale = path.exists() and time.time() - path.stat().st_mtime > 86400
            return load_page(url, timeout, args.refresh or stale)

        openings, type_labels, status = listings.collect(cfg, companies, http_get, kw_regex, name_key, CACHE,
                                                         fetch_page, SSL_CTX, args.refresh)
        counts = {}
        for l in openings:
            counts[l["company_id"]] = counts.get(l["company_id"], 0) + 1
        for c in companies:
            c["openings"] = counts.get(c["id"], 0)

    print("4/4 Sorting and writing results")
    analyse = build_analyser(cfg, insights)
    for c in companies:
        analyse(c)
    companies.sort(key=lambda c: (TIER_ORDER[c["tier"]], -c["score"], c["name"].lower()))
    # Best bets: take students AND work with AI/cloud. Hand-picked organisations that have
    # hosted OsloMet groups, or whose site could not be read, are kept in so they are not lost.
    slots = cfg.get("best_bets", 40)
    for c in companies:
        fits = (c["tier"] == "thesis"
                or (c["tier"] == "students" and (c["matches"]["ai"] or c["matches"]["cloud"])))
        c["best"] = bool(fits and slots > 0 and not c["skip"])
        if c["best"]:
            slots -= 1
        elif c["source"] == "seed" and not c["skip"] and (c["past_client"] or c["tier"] == "unknown"):
            c["best"] = True
    if args.digest:
        print_digest(companies, args.digest)
    rows = [public_row(c) for c in companies]
    write_outputs(rows, cfg, openings, type_labels, status)

    print(f"\n{len(rows)} organisations:")
    for t, label in TIERS.items():
        print(f"  {sum(1 for r in rows if r['tier'] == t):>4}  {label}")
    print(f"  {sum(1 for r in rows if r['best']):>4}  on the Best bets shortlist")
    print(f"  {sum(1 for r in rows if r['internship']):>4}  mention internships or summer jobs")
    print(f"  {len(openings):>4}  open student positions ({sum(1 for l in openings if l['new'])} new)")
    out = ROOT / "results.html"
    print(f"\nResults: {out}")
    fresh = [l for l in openings if l.get("just_found") and l["fit"]["verdict"] != "no"]
    if fresh:
        print(f"New since last run: " + "; ".join(f"{l['title']} ({l['employer']})" for l in fresh))
    for st in status:
        print(f"  source {st['label']}: {st['state']}" + (f" ({st['note']})" if st["note"] else f", {st['count']} positions"))
    if args.background:
        if fresh:
            n = len(fresh)
            notify(f"{n} new internship position{'s' if n != 1 else ''}",
                   "; ".join(f"{l['title']} ({l['employer'] or l['source']})" for l in fresh[:3]) + (" …" if n > 3 else ""))
        broken = [st["label"] for st in status if st["state"] == "failed" and st["fails"] == 3]
        if broken:   # exactly 3 so it is said once, not every two hours
            notify("Internship finder: a source stopped working",
                   ", ".join(broken) + ". Double-click 'Sjekk at alt virker.command' in the folder.")
    elif not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    background = "--background" in sys.argv
    if background:
        print(f"\n=== {time.strftime('%Y-%m-%d %H:%M')} background run")
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nStopped. Pages fetched so far are cached; run again to continue.")
    except Exception as e:
        if not background:
            raise
        # Offline, laptop just woke up, a source is down: try again at the next scheduled run.
        print(f"Run failed, will retry next time: {type(e).__name__}: {e}")
        sys.exit(0)
