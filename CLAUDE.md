# Project & internship finder: guide for whoever works on this next

Read this before changing anything. It is written for an AI assistant or a developer who has not seen the project before. The user-facing manual is `README.md`.

## What this is and who it is for

A personal tool for one student (bachelor in Informasjonsteknologi at OsloMet, Oslo area). It does two jobs and shows both in one local web page, `results.html`:

1. **Bachelor project** (temporary): find organisations that might host a bachelor project in AI/cloud. Will be switched off once a host is found (`--retire-bachelor`).
2. **Internships** (the long-term use): list real openings for students that this person can actually get, track applications, and stay current without anyone maintaining it.

The owner is not a maintainer. Assume nobody reads logs or edits JSON by hand. Everything they do is a double-click on a `.command` file or a click in the page. Keep it that way.

## How to run and check

```
python3 finder.py                 # full run, opens the page
python3 finder.py --no-open       # same, without opening the browser
python3 finder.py --no-listings   # skip job ads (fast, for working on the bachelor side)
python3 finder.py --limit 30      # only 30 registry companies (quick smoke test)
python3 finder.py --doctor        # health check in plain language
python3 -m unittest discover -s tests   # tests, no network needed
```

Python 3.9+, standard library only. **Do not add dependencies**: there is no virtualenv and the owner will not run `pip`.

After any change: run the tests, do one `--no-open` run, and load `results.html` in a browser in both modes (`results.html#bachelor:best`, `results.html#intern:open`). A headless check that has worked well:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --virtual-time-budget=3000 \
  --dump-dom "file://$PWD/results.html#intern:open" | grep -c "<article"
```

## Data flow

```
brreg.no registry ─┐
seeds.json ────────┼─> companies ─> crawl each website (cached) ─> analyse (tier, tags, quotes, ideas)
                   │                         │
                   │                         ├─> job-board links, posting links
NAV job feed ──────┤                         v
Teamtailor feeds ──┼─────────────> listings.collect() ─> eligibility verdict per ad
alert emails ──────┘                         │
                                             v
                          template.html + data ─> results.html (+ results.csv, internships.csv)
```

- `finder.py`: entry point. Registry fetch, website crawl and cache, company analysis (bachelor tiers, tags, project ideas, internship mentions), output, notifications, argument handling.
- `listings.py`: job openings. One function per source, `collect()` combines them and records per-source status. Also date parsing, ad classification and the eligibility check (`eligible()`).
- `email_alerts.py`: reads finn.no / LinkedIn job-alert emails over IMAP. Password in the macOS Keychain.
- `tools.py`: setup wizard, doctor, inbox connect/disconnect, retire/restore the bachelor part.
- `template.html`: the whole page (CSS + JS, no build step, no libraries). `finder.py` replaces `/*__DATA__*/null` with the JSON payload and `/*__BOOKMARKLET__*/""` with `save_job_bookmarklet.js`.
- `config.json`: every tunable (areas, keywords, types, profile, scoring). Prefer adding a keyword here over adding code.
- `insights.json`: hand-written notes per organisation for the bachelor side (dated 2026-11-16).
- `cache/`: registry, pages, NAV feed position (`nav_state.json`), first-seen dates (`seen.json`), source history (`status.json`). Safe to delete; the next run rebuilds it (first run then takes a few minutes and New badges reset).
- `*.command`: double-click wrappers. Norwegian names because the owner is Norwegian.

State the page keeps lives in the browser's localStorage for the `file://` origin: bachelor tracking (`bachelor-finder-v1`), internship tracking (`bachelor-finder-intern-v1`), details (`bachelor-finder-group`), mode, last backup time. **Never rename these keys** without migrating, or the owner silently loses their application history. The page has Back up / Restore for this reason.

## Rules this project follows (keep them)

1. **No scraping of finn.no or LinkedIn.** Both forbid automated access in writing (finn.no robots.txt: "Crawling FINN.no is prohibited unless you have written permission"; LinkedIn robots.txt: automated access "strictly prohibited", job search paths disallowed). Their positions come in only through (a) the sites' own alert emails, read from the owner's inbox, and (b) the "Save job" bookmark the owner clicks in their own browser. If asked to "just scrape them", explain this and improve (a) or (b) instead.
2. **Respect robots.txt** on every company site (`robots_for()` in `finder.py`), identify honestly in the User-Agent, keep requests few (one company at a time per worker, cached).
3. **Never send email or apply on the owner's behalf.** The page only opens a pre-filled draft or copies text.
4. **Harvest only shared mailboxes** (post@, jobb@ ...) or the address a company registered itself; not named employees' addresses (`pick_email()`).
5. **Be honest in the UI about what is a guess**: deadlines read from page text say "from page text, check", project ideas say they are our suggestion, guessed websites are marked, eligibility verdicts show their reason and hidden ads can be revealed.
6. **No secrets in files.** The inbox password is in the Keychain (`email_alerts.keychain_*`).
7. **Nothing is tied to a year.** Seasons, "next summer" and date inference are computed from today (`listings.next_summer`, `nextSummer()` in the page, `parse_date(text, today)`). Do not hardcode years; the tests check 2028 and 2029.
8. **A failing source must not stop the run.** `collect()` catches per source and records it; the page footer and the doctor surface it.

## How to do common things

**Add a job source.** Write a function in `listings.py` returning a list of dicts with the keys documented in `collect()`'s docstring, call `eligible()` for `fit`, and add one line to `sources` in `collect()`. Raise `email_alerts.NotConfigured` for "user has not set this up". Add a test with a recorded sample. Good candidates: other job-board systems that publish feeds (Greenhouse `boards-api.greenhouse.io/v1/boards/<token>/jobs`, Lever `api.lever.co/v0/postings/<company>?mode=json`); the cached pages already contain many links to them (`job_board_links()` finds them, only Teamtailor is read today). Check each one's terms first.

**Change who counts as eligible.** `eligible()` in `listings.py` plus the regexes above it. The profile is `profil` in `config.json` (`nivaa`: bachelor/master, `ferdig`: graduation year). Every rule needs a test in `tests/test_core.py::Eligibility`. Err towards showing: "no" only when the ad is explicit.

**Tune which ads are picked up.** `listings` in `config.json`: `types` (title keywords per type), `it_title`, `it_strong`, `areas`, `days_back`. Keyword syntax (`kw_regex` in `finder.py`): whole words, trailing `*` = any ending, leading `=` or short ALL-CAPS = case-sensitive.

**Change the page.** Edit `template.html` only; `results.html` is generated. Build DOM with the `el()` helper and `textContent` (all data is untrusted text from the web); links go through `safeUrl()`.

## When a source breaks

Run `python3 finder.py --no-open` and read the `source ...: failed (...)` line.

- **NAV feed** (`nav_listings`): the token comes from `https://pam-stilling-feed.nav.no/api/publicToken` on every run (it rotates). 401/403 means the token endpoint or auth scheme changed; check `https://github.com/navikt/pam-stilling-feed`. If the stored position is bad, delete `cache/nav_state.json` (next run re-reads `days_back` days).
- **Registry** (`fetch_registry`): industry codes follow SN2025 (`62.100`, not the older `62.010`). If counts drop to 0, the code scheme changed again.
- **Job boards**: Teamtailor exposes `<board>/jobs.rss`. If that disappears, look for their public jobs JSON.
- **Email alerts**: see the STATUS note at the top of `email_alerts.py`. The parsers were written from the known layout and tested on constructed samples only; no real alert email was available. First thing to do when the owner has connected an inbox: confirm real alerts parse, and add a real one (with personal details removed) to the tests.
- **macOS**: the background job is a launch agent `no.bachlor.finder` (plist in `~/Library/LaunchAgents`, log in `~/Library/Logs/bachelor-finder.log`), installed by `Slå på automatisk oppdatering.command`. If the folder moves, that file must be run again.

## Known weak spots

- Coverage of openings is thin: NAV's feed has few IT student ads; most are on finn.no/LinkedIn (see rule 1) or on job-board systems not read yet.
- Eligibility and IT-relevance are keyword checks. They were hand-verified against all ads present on 2026-11-16, not against future ones.
- About 200 companies have no readable website; for registry entries without a website the domain is guessed from the name (`guess_site`) and can be wrong.
- Bachelor-side tiers come from keywords; `insights.json` corrects the ones that were checked by hand.
- The page's tracking is per browser. A second browser or computer starts empty unless a backup is restored.
- Notifications use `osascript`; macOS may need notifications allowed for Script Editor.

## Sensible next steps (not done)

1. Read Greenhouse and Lever job boards (see "Add a job source").
2. Verify and harden the alert-email parsers on real emails.
3. Let the owner edit the saved search keywords shown as links in the Open positions hint from `config.json`.
4. A weekly summary notification ("3 deadlines this week") from the background run.
5. If the owner starts using several devices: move tracking from localStorage to a file the program reads and writes.

## History

Built in one long session on 2026-11-16 by Claude (Fable 5.2) with the owner. Decisions above come from that session; `git log` has the changes since.
