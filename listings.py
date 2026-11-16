"""Real job openings for students: summer jobs, internships, trainee/graduate
positions, part-time student jobs and thesis offers.

Two sources:
  * NAV's public job feed (arbeidsplassen.no), read incrementally.
  * Teamtailor job boards linked from the company websites finder.py already read.

Used by finder.py; nothing here runs on its own.
"""
import datetime as dt
import email.utils
import html
import json
import re
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import email_alerts

NAV = "https://pam-stilling-feed.nav.no"

# ---------------------------------------------------------------- dates

_MONTHS = {
    "januar": 1, "january": 1, "jan": 1, "februar": 2, "february": 2, "feb": 2, "mars": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11, "desember": 12, "december": 12, "des": 12, "dec": 12,
}
_MON = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?(?!\w)"
_ROLLING = re.compile(r"snarest|asap|fortløpende|løpende|as soon as|rolling|continuous|ongoing", re.I)
_DEADLINE_WORD = re.compile(r"søknadsfrist\w*|søk innen|frist\w*|deadline|apply by|apply before|closing date|"
                            r"applications? close\w*|siste frist", re.I)


def _mk(y, m, d, today):
    try:
        if y is None:
            cand = dt.date(today.year, m, d)
            if cand < today - dt.timedelta(days=30):
                cand = dt.date(today.year + 1, m, d)
            return cand
        y = int(y)
        return dt.date(y + 2000 if y < 100 else y, m, d)
    except ValueError:
        return None


def parse_date(text, today=None):
    """First date found in free text (Norwegian or English), or None."""
    today = today or dt.date.today()
    t = text or ""
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", t)
    if m:
        return _mk(m.group(1), int(m.group(2)), int(m.group(3)), today)
    m = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)", t)
    if m:
        return _mk(m.group(3), int(m.group(2)), int(m.group(1)), today)
    m = re.search(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?" + _MON + r",?\s*(20\d\d)?", t, re.I)
    if m:
        return _mk(m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1)), today)
    m = re.search(r"(?<!\w)" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d),?\s*(20\d\d)?", t, re.I)
    if m:
        return _mk(m.group(3), _MONTHS[m.group(1).lower()], int(m.group(2)), today)
    m = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})\.?(?!\d)", t)
    if m:
        return _mk(None, int(m.group(2)), int(m.group(1)), today)
    return None


def deadline_from_field(text, today=None):
    """Deadline from an employer-supplied field such as 'Snarest' or '01.12.2026'."""
    text = " ".join((text or "").split())
    if not text:
        return None
    d = parse_date(text, today)
    if d:
        return {"date": d.isoformat(), "text": text[:60], "kind": "deadline"}
    if _ROLLING.search(text):
        return {"date": "", "text": text[:60], "kind": "rolling"}
    return None


def deadline_in_text(text, today=None):
    """Best guess at a deadline mentioned in running text ('Søknadsfrist 1. desember')."""
    today = today or dt.date.today()
    for m in _DEADLINE_WORD.finditer(text or ""):
        window = text[m.start(): m.end() + 70]
        d = parse_date(text[m.end(): m.end() + 70], today)
        if d and -90 <= (d - today).days <= 400:
            return {"date": d.isoformat(), "text": " ".join(window.split())[:90], "kind": "guess"}
        if _ROLLING.search(window):
            return {"date": "", "text": " ".join(window.split())[:90], "kind": "rolling"}
    return None


def season_in_text(text):
    years = [int(y) for y in re.findall(r"(?:sommer\w*|summer\w*|internship\w*|trainee\w*|graduate\w*)\s+(20\d\d)",
                                         text or "", re.I)]
    return max(years) if years else None


# ---------------------------------------------------------------- classification

_INTERN_EN = re.compile(r"\b(engineer(ing)?|developer|software|data|science|analyst|tech\w*|IT|AI|ML|security|cloud|"
                        r"product|design\w*|summer|student|research)\s+interns?\b", re.I)


# Titles that are clearly another profession, or not a job posting at all.
_NOT_FOR_US = re.compile(r"sykeplei|helsefag|lege(?!\w)|vekter|kokk|butikk|selger|lærer|barnehage|renhold|sjåfør|"
                         r"mekaniker|\bcv\b|database|nyhetsbrev|newsletter|konkurranse|webinar|podcast", re.I)
_BARE_STUDENT = re.compile(r"studentjobb|studentstilling|studentmedarbeider|studentassistent|studentutvikler|"
                           r"student developer|student worker|deltid|part[- ]time", re.I)


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return " ".join(html.unescape(s).split())


class Classifier:
    def __init__(self, cfg, kw_regex):
        L = cfg["listings"]
        self.types = [(key, spec["label"], [kw_regex(k) for k in spec["keywords"]]) for key, spec in L["types"].items()]
        self.labels = {key: label for key, label, _ in self.types}
        self.it_title = [kw_regex(k) for k in L["it_title"]]
        self.it_strong = [kw_regex(k) for k in L["it_strong"]]
        self.areas = {a.upper() for a in L["areas"]}
        self.profile = cfg.get("profil", {})

    def type_of(self, title):
        if _NOT_FOR_US.search(title):
            return ""
        years = [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", title)]
        if years and max(years) < dt.date.today().year:   # "Summer internship 2025": last year's ad
            return ""
        for key, _, rxs in self.types:
            if any(rx.search(title) for rx in rxs):
                return key
        return "intern" if _INTERN_EN.search(title) else ""

    def it_relevant(self, title, text, categories=()):
        if any(c == "IT" for c in categories) or any(rx.search(title) for rx in self.it_title):
            return True
        return sum(1 for rx in self.it_strong if rx.search(text)) >= 2

    def in_area(self, places):
        places = [p.upper() for p in places if p]
        return not places or any(p in self.areas for p in places)


# ---------------------------------------------------------------- would they take you?

_BACHELOR = re.compile(r"bachelor|3[- ]årig|treårig|høyskole|høgskole|undergraduate|alle studieretninger|"
                       r"all(?:e)? (?:study )?(?:levels|trinn|årstrinn)|uansett (?:årstrinn|studieår|nivå)|"
                       r"(?<!\d)[123]\.\s*(?:(?:eller|og|or|and|[-–/])\s*[2345]\.\s*)?(?:års?|året|klasse|studieår|year)", re.I)
_MASTER = re.compile(r"masterstudent|mastergrad|masternivå|master'?s?[- ](?:degree|student|level|programme|program)|"
                     r"\bM\.?Sc\b|sivilingeniør|siv\.?\s?ing|pursuing a master|integrert master|5[- ]årig|femårig|"
                     r"(?<!\d)[45]\.\s*(?:(?:eller|og|or|and|[-–/])\s*5\.\s*)?(?:års?|året|klasse|studieår|year)|"
                     r"(?:fourth|fifth|4th|5th)[- ]year|fjerde|femte", re.I)
_PHD = re.compile(r"\bph\.?d\b|doktorgrad|postdo[ck]|stipendiat", re.I)
_MY_FIELD_WORDS = re.compile(r"informasjonsteknologi|informatikk|datateknologi|dataingeniør|"
                       r"datavitenskap|datateknikk|computer science|software engineering|information technology|"
                       r"programvareutvikling|systemutvikling|teknologistudent|tech(?:nology)? student|data science|"
                       r"kunstig intelligens|cyber", re.I)
_MY_FIELD_CAPS = re.compile(r"(?<!\w)(IT|IKT)(?!\w)")   # capitals only, so English "it" does not count


class _MyField:
    @staticmethod
    def search(text):
        return _MY_FIELD_WORDS.search(text) or _MY_FIELD_CAPS.search(text)


_MY_FIELD = _MyField
_OTHER_FIELD = re.compile(r"\bjus\w*|\bjuss\w*|juridisk|økonomi\w*|regnskap\w*|revisjon\w*|finans\w*|"
                          r"bygg\w*|anlegg\w*|maskiningeniør|elektro\w*|kjemi\w*|markedsføring|marketing|"
                          r"\bsalg\w*|\bsales\b|\bHR\b|journalist\w*|statsvitenskap|arkitekt(?!ur for)|"
                          r"logistikk\w*|biolog\w*|geolog\w*|pedagog\w*", re.I)
_CLEARANCE = re.compile(r"sikkerhetsklarer\w*|security clearance|norsk statsborger\w*|norwegian citizen\w*", re.I)
_YEAR_LEFT = re.compile(r"(?:ett|et|1|one) år igjen|year left of|penultimate|nest siste (?:år|studieår)|"
                        r"(?:ferdig|uteksaminer\w*|graduat\w*|fullfør\w*)[^.]{0,40}(20\d\d)", re.I)
_EXPERIENCE = re.compile(r"(?:minimum|minst|at least)?\s*(\d+)\s*\+?\s*(?:års?|years?)['’]?\s+(?:of\s+)?(?:relevant\s+)?"
                         r"(?:arbeids|yrkes|work(?:ing)?\s+|professional\s+)?(?:erfaring|experience)", re.I)


def eligible(profile, title, text, kind=""):
    """Would this position take the applicant in config 'profil'?
    Returns {"verdict": "ok" | "check" | "no", "reason": ..., "field": bool}.
    Keyword-based: 'no' only when the ad is explicit, 'check' for conditions we cannot know."""
    title, text = title or "", text or ""
    both = title + " . " + text
    is_master = profile.get("nivaa") == "master"
    grad_year = int(profile.get("ferdig") or 0)
    field = bool(_MY_FIELD.search(both))
    out = lambda verdict, reason: {"verdict": verdict, "reason": reason, "field": field}

    if _PHD.search(title):
        return out("no", "PhD or research position")
    bachelor, master = _BACHELOR.search(both), _MASTER.search(both)
    if kind == "thesis" and not is_master and re.search(r"master", title, re.I) and not re.search(r"bachelor", both, re.I):
        return out("no", "master thesis offer")
    if master and not bachelor and not is_master:
        return out("no", "asks for master students (“" + " ".join(master.group(0).split()) + "”)")
    m = _EXPERIENCE.search(both)
    if m and int(m.group(1)) >= 2:
        return out("no", "asks for " + m.group(1) + " years of work experience")
    other = _OTHER_FIELD.search(title)
    if other and not _MY_FIELD.search(title):
        return out("no", "aimed at another field (“" + other.group(0) + "”)")

    checks = []
    c = _CLEARANCE.search(both)
    if c:
        checks.append("requires " + ("security clearance" if "klarer" in c.group(0).lower() or "clearance" in c.group(0).lower()
                                     else "Norwegian citizenship"))
    y = _YEAR_LEFT.search(both)
    if y and kind in ("summer", "intern"):
        year = int(y.group(1)) if y.group(1) else 0
        if not year or (grad_year and year > grad_year):
            checks.append("may want students who still have a year left (you finish in " + str(grad_year) + ")")
    if checks:
        return out("check", "; ".join(checks))
    if bachelor:
        return out("ok", "open to bachelor students")
    return out("ok", "names your field" if field else "no level or field requirement stated")


def context_around(text, regexes, width=350):
    """The parts of a long page that are about the keywords (for judging a company programme)."""
    spans = []
    for rx in regexes:
        for n, m in enumerate(rx.finditer(text)):
            if n >= 6:
                break
            spans.append(text[max(0, m.start() - width): m.end() + width])
    return " … ".join(spans)


# ---------------------------------------------------------------- NAV feed

def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _get_retry(http_get, url, headers, tries=3):
    last = None
    for _ in range(tries):
        try:
            return http_get(url, 60, accept="application/json", max_bytes=40_000_000, headers=headers)[2]
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise
            last = e
        except Exception as e:
            last = e
    raise last


def nav_listings(cfg, clf, http_get, cache_dir, refresh, log):
    L = cfg["listings"]
    path = cache_dir / "nav_state.json"
    state = {} if refresh else _load(path, {})
    state.setdefault("cands", {})
    state.setdefault("details", {})

    token = http_get(NAV + "/api/publicToken", 20, accept="text/plain")[2].strip().splitlines()[-1].strip()
    auth = {"Authorization": "Bearer " + token}

    # 1. Walk the change feed from where we stopped last time.
    url, headers = state.get("resume_url"), dict(auth)
    if not url:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=L["days_back"])
        headers["If-Modified-Since"] = email.utils.format_datetime(since, usegmt=True)
        url = "/api/v1/feed"
        log(f"  first run: reading {L['days_back']} days of job ads from NAV (a few minutes) ...")
    pages = 0
    while url:
        d = json.loads(_get_retry(http_get, NAV + url, headers))
        headers = dict(auth)
        for it in d.get("items", []):
            e = it.get("_feed_entry") or {}
            uid = e.get("uuid") or it.get("id")
            if e.get("status") != "ACTIVE":
                state["cands"].pop(uid, None)
                state["details"].pop(uid, None)
                continue
            title = e.get("title") or ""
            if clf.type_of(title) and clf.in_area([e.get("municipal")]):
                state["cands"][uid] = {"title": title, "business": e.get("businessName") or "",
                                       "modified": e.get("sistEndret") or it.get("date_modified") or ""}
        # The newest page keeps growing, so it is read again next time.
        state["resume_url"] = d.get("feed_url") or url
        url = d.get("next_url")
        pages += 1
        if pages % 25 == 0:
            _save(path, state)
            log(f"  NAV feed: {pages} pages read, {len(state['cands'])} student-type ads in the area so far")
    _save(path, state)

    # 2. Fetch details for candidates we have not seen (or that changed).
    todo = [u for u, c in state["cands"].items()
            if state["details"].get(u, {}).get("modified") != c["modified"]]

    def detail(uid):
        try:
            d = json.loads(_get_retry(http_get, f"{NAV}/api/v1/feedentry/{uid}", auth))
        except Exception:
            return uid, None
        a = d.get("ad_content") or {}
        emp = a.get("employer") or {}
        locs = a.get("workLocations") or []
        return uid, {
            "modified": state["cands"][uid]["modified"],
            "status": d.get("status"),
            "title": a.get("title") or state["cands"][uid]["title"],
            "employer": emp.get("name") or state["cands"][uid]["business"],
            "orgnr": str(emp.get("orgnr") or ""),
            "homepage": emp.get("homepage") or "",
            "text": strip_html(a.get("description"))[:3000],
            "due": a.get("applicationDue") or "",
            "expires": (a.get("expires") or "")[:10],
            "published": (a.get("published") or "")[:10],
            "apply": a.get("applicationUrl") or "",
            "link": a.get("link") or "",
            "engagement": a.get("engagementtype") or "",
            "extent": a.get("extent") or "",
            "places": [x for loc in locs for x in (loc.get("municipal"), loc.get("city")) if x],
            "cats": [c.get("level1") for c in (a.get("occupationCategories") or []) if c.get("level1")],
        }

    if todo:
        log(f"  NAV: fetching {len(todo)} new or changed ads")
        with ThreadPoolExecutor(max_workers=8) as pool:
            for uid, det in pool.map(detail, todo):
                if det is None or det["status"] != "ACTIVE":
                    state["cands"].pop(uid, None)
                    state["details"].pop(uid, None)
                else:
                    state["details"][uid] = det
        _save(path, state)

    # 3. Turn the relevant ones into listings.
    today = dt.date.today()
    out, stats = [], {"candidates": len(state["cands"]), "it_relevant": 0}
    for uid, det in state["details"].items():
        if uid not in state["cands"]:
            continue
        kind = clf.type_of(det["title"])
        if not kind or not clf.in_area(det["places"]):
            continue
        if not clf.it_relevant(det["title"], det["text"], det["cats"]):
            continue
        stats["it_relevant"] += 1
        deadline = deadline_from_field(det["due"], today)
        if not deadline and det["expires"]:
            deadline = {"date": det["expires"], "text": "ad expires", "kind": "expires"}
        if det["expires"] and det["expires"] < (today - dt.timedelta(days=14)).isoformat():
            continue
        places = []
        for p in det["places"]:
            p = p.title()
            if p not in places:
                places.append(p)
        out.append({
            "id": "nav:" + uid, "source": "arbeidsplassen.no", "title": det["title"], "employer": det["employer"],
            "orgnr": det["orgnr"], "homepage": det["homepage"], "location": ", ".join(places[:3]),
            "type": kind, "deadline": deadline, "published": det["published"],
            "url": det["apply"] or det["link"], "ad_url": det["link"],
            "excerpt": det["text"][:260], "extent": det["extent"], "company_id": "",
            "fit": eligible(clf.profile, det["title"], det["text"], kind),
        })
    log(f"  NAV: {stats['candidates']} student-type ads in the area, {len(out)} of them IT-relevant")
    return out


# ---------------------------------------------------------------- company job boards (Teamtailor)

_BOARD_SUB = {"karriere", "career", "careers", "jobs", "jobb", "job", "join"}
_BOARD_HOSTS = ("teamtailor.com", "myworkdayjobs.com", "greenhouse.io", "lever.co", "recman.no", "webcruiter.no",
                "webcruiter.com", "jobylon.com", "varbi.com", "reachmee.com", "smartrecruiters.com", "hrmanager.no",
                "easycruit.com")
_OSLO_WORDS = re.compile(r"oslo|lysaker|fornebu|bærum|asker|sandvika|lillestrøm|skøyen|billingstad|nydalen", re.I)
_ELSEWHERE = re.compile(r"bergen|trondheim|stavanger|haugesund|kristiansand|tromsø|sandnes|arendal|grenland|sandefjord|"
                        r"førde|ålesund|bodø|drammen|fredrikstad|tønsberg|hamar|lillehammer|molde|stockholm|göteborg|"
                        r"gothenburg|malmö|copenhagen|københavn|helsinki|london|berlin|amsterdam", re.I)


def job_board_links(company):
    """(first job-board URL to show the user, candidate Teamtailor origins to try)."""
    show, origins = "", []
    for p in company.get("pages", []):
        for href, _ in p["links"]:
            try:
                u = urllib.parse.urlsplit(urllib.parse.urljoin(p["final_url"], href))
            except ValueError:
                continue
            host = (u.hostname or "").lower()
            if u.scheme not in ("http", "https") or not host:
                continue
            known = any(host == h or host.endswith("." + h) for h in _BOARD_HOSTS)
            sub = host.split(".")[0] in _BOARD_SUB
            if not (known or sub):
                continue
            if not show:
                show = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))
            if (sub or host.endswith(".teamtailor.com")) and "workday" not in host:
                origin = f"https://{host}"
                if origin not in origins and len(origins) < 2:
                    origins.append(origin)
    return show, origins


def board_listings(cfg, clf, companies, http_get, log):
    jobs, seen_origins = [], set()
    for c in companies:
        for origin in c.get("board_origins", []):
            if origin not in seen_origins:
                seen_origins.add(origin)
                jobs.append((origin, c))

    def fetch(job):
        origin, c = job
        try:
            _, ctype, body = http_get(origin + "/jobs.rss", 10, accept="application/rss+xml,application/xml;q=0.9",
                                      max_bytes=3_000_000)
        except Exception:
            return []
        if "xml" not in ctype and not body.lstrip().startswith(("<?xml", "<rss")):
            return []
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            return []
        found = []
        for item in root.iter("item"):
            title = " ".join((item.findtext("title") or "").split())
            kind = clf.type_of(title)
            if not kind:
                continue
            cities, remote = [], ""
            for node in item.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "city" and node.text:
                    cities.append(node.text.strip())
                elif tag == "remoteStatus" and node.text:
                    remote = node.text.strip().lower()
            if cities and not any(_OSLO_WORDS.search(x) for x in cities) and remote != "fully":
                continue
            text = strip_html(item.findtext("description"))
            # An IT company's own summer/graduate programme is relevant even if the ad text is generic.
            own_programme = c.get("source") == "registry" and kind != "parttime"
            if not own_programme and not clf.it_relevant(title, text):
                continue
            link = (item.findtext("link") or "").strip()
            if not link.startswith("http"):
                continue
            pub = email.utils.parsedate_to_datetime(item.findtext("pubDate")).date().isoformat() \
                if item.findtext("pubDate") else ""
            found.append({
                "id": "board:" + link, "source": "company job board", "title": title, "employer": c["name"],
                "orgnr": c.get("orgnr", ""), "homepage": c.get("website", ""),
                "location": ", ".join(dict.fromkeys(cities)),
                "type": kind, "deadline": deadline_in_text(text), "published": pub, "url": link, "ad_url": link,
                "excerpt": text[:260], "extent": "", "company_id": c["id"],
                "fit": eligible(clf.profile, title, text, kind),
            })
        return found

    out, boards = [], 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        for found in pool.map(fetch, jobs):
            boards += bool(found)
            out.extend(found)
    log(f"  job boards: tried {len(jobs)}, {len(out)} student-type positions on {boards} of them")
    return out


# ---------------------------------------------------------------- postings linked on company websites

def site_listings(cfg, clf, companies, fetch_page, log):
    """Job postings that a company links to from the pages we already read, e.g. a link
    reading 'Sommerjobb 2027 - utvikler'. The linked page is fetched to look for a deadline."""
    cands = []
    for c in companies:
        seen, n = set(), 0
        for p in c.get("pages", []):
            for href, text in p["links"]:
                text = re.sub(r"^(les mer|read more|se mer|læs mere)\s*:?\s*", "", " ".join(text.split()), flags=re.I)
                if not (12 <= len(text) <= 110) or len(text.split()) < 2 or text in seen:
                    continue
                kind = clf.type_of(text)
                if not kind or c.get("skip"):
                    continue
                if kind == "parttime" and not _BARE_STUDENT.search(text):   # "students" in a news headline
                    continue
                # A posting names a year or a role; a bare "Studenter" or "Graduate" is a menu item.
                if not (re.search(r"20\d\d", text) or any(rx.search(text) for rx in clf.it_title)):
                    continue
                if _ELSEWHERE.search(text) and not _OSLO_WORDS.search(text):
                    continue
                try:
                    url = urllib.parse.urldefrag(urllib.parse.urljoin(p["final_url"], href))[0]
                except ValueError:
                    continue
                if not url.startswith("http") or url.rstrip("/") == p["final_url"].rstrip("/"):
                    continue
                seen.add(text)
                cands.append((c, kind, text, url))
                n += 1
                if n >= 4:
                    break
            if n >= 4:
                break

    def build(cand):
        c, kind, title, url = cand
        page = fetch_page(url)
        if page and page.get("error") in ("HTTP 404", "HTTP 410"):   # dead link: the posting is gone
            return None
        text = page["text"] if page and page.get("ok") else ""
        if text and not clf.it_relevant(title, text):
            return None
        if text and _ELSEWHERE.search(text[:400]) and not _OSLO_WORDS.search(text[:1500]):
            return None
        return {
            "id": "site:" + url, "source": "company website", "title": title, "employer": c["name"],
            "orgnr": c.get("orgnr", ""), "homepage": c.get("website", ""), "location": c.get("municipality", ""),
            "type": kind, "deadline": deadline_in_text(text), "published": "", "url": url, "ad_url": url,
            "excerpt": (page.get("description") or "")[:260] if page else "", "extent": "", "company_id": c["id"],
            "fit": eligible(clf.profile, title, text[:6000], kind),
        }

    out = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        out = [l for l in pool.map(build, cands) if l]
    log(f"  company websites: {len(cands)} posting-like links, {len(out)} kept")
    return out


# ---------------------------------------------------------------- combine

def _norm(s):
    words = re.findall(r"\w+", (s or "").lower())
    return " ".join(w for w in words if w not in {"as", "asa", "norge", "norway", "no"})


def alert_listings(cfg, clf, cache_dir, ssl_ctx, log):
    """Positions from finn.no / LinkedIn job-alert emails (see email_alerts.py)."""
    out = []
    for it in email_alerts.fetch(cfg, cache_dir, ssl_ctx):
        # The user's saved search already decides what is relevant, so an alert position is
        # kept even when its title does not look like a student job.
        kind = clf.type_of(it["title"]) or "alert"
        out.append({
            "id": it["id"], "source": it["source"], "title": it["title"], "employer": it.get("employer", ""),
            "orgnr": "", "homepage": "", "location": it.get("location", ""), "type": kind, "deadline": None,
            "published": it.get("published", ""), "url": it["url"], "ad_url": it["url"], "excerpt": "",
            "extent": "", "company_id": "", "fit": eligible(clf.profile, it["title"], "", kind),
        })
    log(f"  email alerts: {len(out)} positions")
    return out


def collect(cfg, companies, http_get, kw_regex, name_key, cache_dir, fetch_page, ssl_ctx, refresh=False, log=print):
    """All current student-type openings plus a status report per source.

    ADDING A SOURCE: write a function that returns a list of listing dicts with the keys
    used below (id, source, title, employer, orgnr, homepage, location, type, deadline,
    published, url, ad_url, excerpt, extent, company_id, fit) and add one line to
    `sources`. Raise email_alerts.NotConfigured if the user has not set it up. Anything
    else it raises is recorded as a failure and the other sources still run.
    """
    clf = Classifier(cfg, kw_regex)
    sources = [
        ("nav", "arbeidsplassen.no (NAV)", lambda: nav_listings(cfg, clf, http_get, cache_dir, refresh, log)),
        ("boards", "Company job boards", lambda: board_listings(cfg, clf, companies, http_get, log)),
        ("sites", "Company websites", lambda: site_listings(cfg, clf, companies, fetch_page, log)),
        ("email", "finn.no and LinkedIn alerts (email)", lambda: alert_listings(cfg, clf, cache_dir, ssl_ctx, log)),
    ]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    status_path = cache_dir / "status.json"
    history = _load(status_path, {})
    listings, status = [], []
    for key, label, fn in sources:
        h = history.setdefault(key, {"last_ok": "", "fails": 0})
        entry = {"key": key, "label": label, "state": "ok", "count": 0, "note": ""}
        try:
            found = fn()
            listings.extend(found)
            entry["count"] = len(found)
            h["last_ok"], h["fails"] = now, 0
        except email_alerts.NotConfigured as e:
            entry["state"], entry["note"] = "off", str(e)
            h["fails"] = 0
        except Exception as e:
            entry["state"], entry["note"] = "failed", f"{type(e).__name__}: {str(e)[:120]}"
            h["fails"] += 1
            log(f"  {label} failed and was skipped: {entry['note']}")
        entry["last_ok"], entry["fails"] = h["last_ok"], h["fails"]
        status.append(entry)
    _save(status_path, history)

    # Link to our company rows, then drop duplicates (same employer and title).
    by_orgnr = {c["orgnr"]: c for c in companies if c.get("orgnr")}
    by_name = {name_key(c["name"]): c for c in companies}
    seen_keys, out = set(), []
    for l in listings:
        if not l["company_id"]:
            c = by_orgnr.get(l["orgnr"]) or (by_name.get(name_key(l["employer"])) if l["employer"] else None)
            if c:
                l["company_id"] = c["id"]
        key = (_norm(l["employer"]), _norm(l["title"]))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(l)

    # First-seen bookkeeping for the "New" badge.
    today = dt.date.today().isoformat()
    seen_path = cache_dir / "seen.json"
    seen = _load(seen_path, {})
    seen.setdefault("first_run", today)
    ids = seen.setdefault("ids", {})
    had_history = bool(ids)
    for l in out:
        l["just_found"] = had_history and l["id"] not in ids   # appeared since the previous run
        l["first_seen"] = ids.setdefault(l["id"], today)
        l["new"] = l["first_seen"] > seen["first_run"]
    _save(seen_path, seen)

    far = "9999-12-31"
    out.sort(key=lambda l: ((l["deadline"] or {}).get("date") or far, not l["fit"]["field"], l["employer"].lower()))
    labels = dict(clf.labels)
    labels["alert"] = "From your saved search"
    return out, labels, status


def next_summer(today=None):
    """The summer people are applying for now: this year's until June, next year's after."""
    today = today or dt.date.today()
    return today.year if today.month <= 6 else today.year + 1
