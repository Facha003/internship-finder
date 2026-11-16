# Project & internship finder

One page with two modes, switched at the top right and remembered between visits:

- **Bachelor project**: organisations around Oslo that might host our bachelor project (AI / cloud).
- **Internships**: real openings for students (summer jobs, internships, trainee/graduate, part-time, thesis), plus companies that say they take interns.

## Use it

Double-click **Finn bedrifter.command** (or run `python3 finder.py`). It fetches everything and opens the page. The very first run takes a few minutes; later runs about a minute.

### Automatic updates

Double-click **Slå på automatisk oppdatering.command** once. After that the program runs by itself every 2 hours while you are logged in to this Mac (and at login), without opening anything. When it finds a new position you can apply to, macOS shows a notification with the title and employer. A page left open in the browser reloads itself every 15 minutes.

**Slå av automatisk oppdatering.command** turns it off. The log of the background runs is in `~/Library/Logs/bachelor-finder.log`. If you move this folder, run the "på" file again from the new place.

### The double-click files

| File | What it does |
|---|---|
| `Finn bedrifter.command` | Update everything now and open the page. |
| `Slå på / Slå av automatisk oppdatering.command` | Turn the 2-hourly background update on or off. |
| `Oppsett.command` | Set your profile: study programme, bachelor/master, graduation year, where you want to work. Change it when your situation changes; the filtering follows. |
| `Koble til e-post.command` | Connect the inbox that receives finn.no and LinkedIn job alerts (see below). |
| `Sjekk at alt virker.command` | Health check. Says what is wrong and how to fix it. Start here if something looks off. |
| `Ferdig med bachelorprosjekt.command` | When you have found a project: switches the tool to internships only. |

### Positions from finn.no and LinkedIn

Neither site allows programs to read its pages, so this tool does not. Two ways to get their positions in:

1. **Job alerts by email (automatic).** On finn.no and on LinkedIn, make a saved job search (for example "internship" / "sommerjobb", IT, Oslo) and turn on email alerts for it. Then double-click `Koble til e-post.command` and give it the email address and a Gmail *app password* (the file explains how to make one; it needs 2-step verification on the Google account). The password is stored in the macOS Keychain. From then on, positions in those alert emails show up under Open positions like the others. Only emails from finn.no and LinkedIn are read, and nothing in the inbox is changed. This reader was built from the known layout of those emails and has not yet been tried on real ones; if alerts arrive but nothing shows up, that is the first thing to have fixed (see `CLAUDE.md`).
2. **Save job button (one click).** In Internships, My applications, open "Add a position yourself". Drag the **Save job** link to your bookmarks bar. When you are looking at an ad on any site, click the bookmark, then paste into the first box. Title, employer, link and deadline are filled in. You can also type a position in by hand.

### When you have found a bachelor project

Double-click `Ferdig med bachelorprosjekt.command`. The bachelor mode disappears from the page, the bachelor-only files move to an `arkiv` folder, and the tool becomes "Internship finder". The page then offers to download your notes about who you contacted. To undo, run `Oppsett.command` and answer yes to showing the bachelor part.

### Keeping your data

Your applications, notes and details are stored in your browser, not in this folder. Use **Back up my data** at the bottom of the page now and then (the page reminds you after two weeks), and **Restore from backup** on a new computer or browser. **All my deadlines to calendar** and **Add to calendar** give a calendar file with a reminder two days before each deadline.

The bottom of the page also shows which sources worked in the last update, and the top shows a warning if the page is more than two days old.

Fill in the details box at the top once. It is saved in your browser and used in the emails.

Needs Python 3.9+ and nothing else.

### Bachelor project mode

- **Best bets**: the shortlist. Each card has an availability badge, what they do and work with, a quote from their own site about students (with source link), and a project idea that is also written into the email.
- Press **Email** or **Copy email text**, then set the status. The card moves to **Contacted**.
- **All** has every organisation that was checked.

### Internships mode

- Only positions you can get are shown. The program reads each ad against your profile in `config.json` (`profil`: study programme, level, graduation year; set with `Oppsett.command`) and hides ads that ask for master or PhD students, several years of work experience, or another field. Conditions it cannot know about you, such as Norwegian citizenship or needing a year left of your studies, get a yellow **Check** badge. Tick "Show ones I probably can't get" to see the hidden ones with the reason. When you start a master, run `Oppsett.command` and the master-only positions come back.
- **Open positions**: actual job ads, soonest deadline first. Each card shows the deadline (red when a week or less is left), type, employer, location and an apply link. A **New** badge marks ads that appeared within the last week; "New only" filters to those. Closed ads are hidden unless you tick "Show closed".
- **Companies with programmes**: companies whose own site mentions internships, summer jobs or trainee programmes, even with no ad out right now. Shows any deadline or year found on the page, a link to where to apply, their job board, and an individual email (`internship_template.txt`).
- **My applications**: everything you set a status on (Saved, Applied, Interview, Offer, Rejected), with notes. An ad you saved stays here even after it disappears from the listings.

Internship tracking is separate from the bachelor project tracking and is personal: each of us has our own in our own browser. **Export tracking** downloads the current mode's statuses and notes.

## Where the data comes from

| Source | Used for |
|---|---|
| Brønnøysund company registry | IT companies in Oslo, Bærum, Asker, Lillestrøm with 10+ employees |
| `seeds.json` | Hand-picked organisations the registry search misses |
| Each company's website | Description, technologies, student and internship mentions, links to job postings |
| NAV's public job feed (arbeidsplassen.no) | Job ads from all employers, last 90 days, read incrementally |
| Company job boards (Teamtailor) | Open student positions at the companies above |
| Your inbox (optional) | finn.no and LinkedIn job-alert emails |

## What to trust

- **Deadlines** from arbeidsplassen.no are the employer's own field. Deadlines marked "from page text, check" were picked out of running text and can be wrong.
- **Open positions is not complete.** Many employers only advertise on finn.no, LinkedIn or their own site; the tab links to searches on those. Job boards other than Teamtailor (Workday, Greenhouse, Recman, Webcruiter) are linked from the company card but not read.
- The "would they take me" verdict is a keyword reading of the ad text. An ad that says nothing about level is shown. It can be wrong in both directions, so glance at the hidden ones now and then.
- An ad is kept if its title looks like a student position, it is in the Oslo area, and it looks IT-relevant. All three are keyword checks (`listings` in `config.json`), so expect an occasional odd one.
- **Quotes** are the organisation's own text. **Project ideas** are our suggestions, not requests from them; the hand-written ones are in `insights.json` (2026-11-16), the rest are generic.
- **Badges** in bachelor mode come from keywords and can be wrong in both directions. About 200 organisations have no readable website.

## Files

| File | What it is |
|---|---|
| `Slå på/av automatisk oppdatering.command` | Turn the 2-hourly background run on or off. |
| `finder.py` | The program. `--background` is what the scheduled run uses (no browser, notification on new positions). `--limit 30` quick test, `--refresh` fetch everything again, `--no-listings` skip job ads, `--no-open` do not open the page, `--digest 40` print website excerpts for the shortlist. |
| `listings.py` | Fetches and filters the job openings. |
| `email_alerts.py` | Reads finn.no / LinkedIn job-alert emails. |
| `tools.py` | Setup, health check, inbox connection, retiring the bachelor part. |
| `save_job_bookmarklet.js` | The code behind the Save job bookmark. |
| `tests/` | Automatic checks; the health check runs them. |
| `CLAUDE.md` | Guide for an AI assistant or developer who continues this work: how it is built, the rules it follows, what to do when a source breaks, and what to build next. |
| `config.json` | Search area, keywords, tags, project ideas, and the `listings` settings (days back, areas, types, IT keywords). |
| `seeds.json` | Hand-picked organisations. |
| `insights.json` | Hand-written description and project idea per organisation; can also correct a badge (`tier`), hide a quote (`hide_quote`) or drop an entry from Best bets (`skip`). |
| `email_template.txt`, `internship_template.txt` | The Norwegian emails for each mode. |
| `template.html` | Layout of the results page. |
| `results.html`, `results.csv`, `internships.csv` | Output. Regenerated on every run. |
| `cache/` | Saved registry data, web pages (refreshed after 7 days), the position in NAV's feed, and when each ad was first seen. Deleting it makes the next run start from scratch and resets the New badges. |
