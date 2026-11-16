"""Maintenance commands, reached through finder.py flags and the .command files:

    --setup             Oppsett.command               answer questions to set your profile
    --doctor            Sjekk at alt virker.command   check everything, say how to fix problems
    --connect-email     Koble til e-post.command      inbox for finn.no / LinkedIn job alerts
    --disconnect-email
    --retire-bachelor   Ferdig med bachelorprosjekt.command   internships only (reversible)
    --restore-bachelor

Each function takes `env` (root, cache, ssl_ctx, http_get) from finder.py and returns
nothing. They only print and edit config.json; none of them deletes user data.
"""
import datetime as dt
import getpass
import json
import shutil
import subprocess
import sys
import time

import email_alerts

LABEL = "no.bachlor.finder"
BACHELOR_ONLY_FILES = ["insights.json", "email_template.txt", "results.csv"]


def _cfg_path(env):
    return env["root"] / "config.json"


def load_cfg(env):
    return json.loads(_cfg_path(env).read_text(encoding="utf-8"))


def save_cfg(env, cfg):
    _cfg_path(env).write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ask(question, default=""):
    try:
        answer = input(f"{question} [{default}]: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _yes(question, default=True):
    return _ask(question + " (j/n)", "j" if default else "n").lower().startswith(("j", "y"))


# ---------------------------------------------------------------- setup

def setup(env):
    cfg = load_cfg(env)
    p = cfg.setdefault("profil", {})
    print("Oppsett. Trykk Enter for å beholde verdien i [klammer].\n")
    p["studieprogram"] = _ask("Studieprogram", p.get("studieprogram", "informasjonsteknologi"))
    p["studiested"] = _ask("Studiested", p.get("studiested", "OsloMet"))
    level = _ask("Nivå du studerer på nå (bachelor/master)", p.get("nivaa", "bachelor")).lower()
    p["nivaa"] = "master" if level.startswith("m") else "bachelor"
    year = _ask("Året du er ferdig med graden", str(p.get("ferdig", dt.date.today().year + 1)))
    p["ferdig"] = int(year) if year.isdigit() else p.get("ferdig")
    areas = _ask("Kommuner/steder du vil jobbe i (kommaseparert)", ", ".join(cfg["listings"]["areas"]))
    cfg["listings"]["areas"] = [a.strip().upper() for a in areas.split(",") if a.strip()]
    cfg.setdefault("gruppe", {})["studieprogram"] = p["studieprogram"]
    want_bachelor = _yes("Vis bachelorprosjekt-delen?", cfg.get("bachelor", True))
    save_cfg(env, cfg)
    if want_bachelor != cfg.get("bachelor", True):
        (restore_bachelor if want_bachelor else retire_bachelor)(env)
    print("\nLagret. Kjør 'Finn bedrifter.command' for å oppdatere siden med de nye valgene.")


# ---------------------------------------------------------------- bachelor part on/off

def retire_bachelor(env):
    cfg = load_cfg(env)
    archive = env["root"] / "arkiv"
    archive.mkdir(exist_ok=True)
    moved = []
    for name in BACHELOR_ONLY_FILES:
        src = env["root"] / name
        if src.exists():
            shutil.move(str(src), str(archive / name))
            moved.append(name)
    cfg["bachelor"] = False
    save_cfg(env, cfg)
    print("Bachelorprosjekt-delen er slått av. Verktøyet viser nå bare internships.")
    if moved:
        print("Flyttet til mappen 'arkiv': " + ", ".join(moved))
    print("Notatene dine om hvem dere kontaktet ligger i nettleseren. Åpne siden én gang til: den tilbyr å\n"
          "laste dem ned som en fil, så de ikke går tapt.")
    print("Angre: kjør 'Oppsett.command' og svar ja på 'Vis bachelorprosjekt-delen?'.")
    print("\nKjør 'Finn bedrifter.command' for å oppdatere siden.")


def restore_bachelor(env):
    cfg = load_cfg(env)
    archive = env["root"] / "arkiv"
    back = []
    for name in BACHELOR_ONLY_FILES:
        src = archive / name
        if src.exists() and not (env["root"] / name).exists():
            shutil.move(str(src), str(env["root"] / name))
            back.append(name)
    cfg["bachelor"] = True
    save_cfg(env, cfg)
    print("Bachelorprosjekt-delen er slått på igjen." + (" Hentet tilbake: " + ", ".join(back) if back else ""))
    print("Kjør 'Finn bedrifter.command' for å oppdatere siden.")


# ---------------------------------------------------------------- inbox

def connect_email(env):
    cfg = load_cfg(env)
    e = cfg.setdefault("epost", {"adresse": "", "imap_server": "imap.gmail.com", "dager_tilbake": 30})
    print("Koble til innboksen som mottar jobbvarsler fra finn.no og LinkedIn.\n"
          "Verktøyet leser bare e-post fra disse to avsenderne, og endrer ingenting i innboksen.\n\n"
          "For Gmail trenger du et app-passord (ikke det vanlige passordet):\n"
          "  1. Slå på 2-trinnsbekreftelse på Google-kontoen.\n"
          "  2. Gå til https://myaccount.google.com/apppasswords og lag et passord, f.eks. 'Internship finder'.\n"
          "  3. Lim inn de 16 bokstavene under.\n")
    address = _ask("E-postadresse", e.get("adresse", ""))
    server = _ask("IMAP-server", e.get("imap_server", "imap.gmail.com"))
    try:
        password = getpass.getpass("App-passord (vises ikke mens du skriver): ").replace(" ", "")
    except EOFError:
        password = ""
    if not address or not password:
        print("Avbrutt: adresse og passord trengs.")
        return
    try:
        box = email_alerts.login(address, password, server, env["ssl_ctx"])
        box.logout()
    except Exception as err:
        print(f"\nInnlogging feilet: {err}\nSjekk adressen og at du brukte et app-passord. Ingenting ble lagret.")
        return
    try:
        email_alerts.keychain_set(address, password)
    except Exception as err:
        print(f"\n{err}")
        return
    e.update(adresse=address, imap_server=server)
    save_cfg(env, cfg)
    print("\nKoblet til. Passordet ligger i macOS Nøkkelring, ikke i noen fil.\n"
          "Husk å lage lagrede søk med e-postvarsel på finn.no og LinkedIn (se README).")


def disconnect_email(env):
    cfg = load_cfg(env)
    address = (cfg.get("epost") or {}).get("adresse", "")
    if address:
        email_alerts.keychain_delete(address)
        cfg["epost"]["adresse"] = ""
        save_cfg(env, cfg)
    print("Innboksen er koblet fra, og passordet er slettet fra Nøkkelring.")


# ---------------------------------------------------------------- doctor

def doctor(env):
    """Plain-language health check. Prints OK / PROBLEM lines; exit code 1 if any problem."""
    problems = 0

    def ok(msg):
        print("  OK       " + msg)

    def bad(msg, fix):
        nonlocal problems
        problems += 1
        print("  PROBLEM  " + msg + "\n           -> " + fix)

    def info(msg):
        print("  -        " + msg)

    print("Sjekker at alt virker ...\n")
    v = sys.version_info
    if v >= (3, 9):
        ok(f"Python {v.major}.{v.minor}")
    else:
        bad(f"Python {v.major}.{v.minor} is too old", "Install Python 3.9 or newer from python.org.")

    try:
        cfg = load_cfg(env)
        ok("config.json can be read")
    except Exception as err:
        bad(f"config.json is broken ({err})", "Undo your last edit to config.json, or restore it with 'git checkout config.json'.")
        return 1

    for label, url in (("Company registry (brreg.no)", "https://data.brreg.no/enhetsregisteret/api/enheter?size=1"),
                       ("NAV job feed", "https://pam-stilling-feed.nav.no/api/publicToken")):
        try:
            env["http_get"](url, 20, accept="*/*")
            ok(label + " answers")
        except Exception as err:
            bad(f"{label} does not answer ({type(err).__name__})",
                "Check the internet connection. If it works in a browser but not here for days, the service has "
                "changed and the code needs an update (see CLAUDE.md).")

    status = {}
    try:
        status = json.loads((env["cache"] / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info("No source history yet (the program has not completed a run).")
    names = {"nav": "arbeidsplassen.no (NAV)", "boards": "Company job boards", "sites": "Company websites",
             "email": "finn.no / LinkedIn alerts"}
    for key, h in status.items():
        if h.get("fails", 0) >= 3:
            bad(f"{names.get(key, key)} has failed {h['fails']} runs in a row (last worked: {h.get('last_ok') or 'never'})",
                "Run 'python3 finder.py --no-open' in Terminal and read the line that says 'failed'. See CLAUDE.md, "
                "section 'When a source breaks'.")
        elif h.get("last_ok"):
            ok(f"{names.get(key, key)} last worked {h['last_ok']}")

    try:
        email_alerts.settings(cfg)
        address, password, server, _ = email_alerts.settings(cfg)
        try:
            email_alerts.login(address, password, server, env["ssl_ctx"]).logout()
            ok(f"Inbox {address} can be opened")
        except Exception as err:
            bad(f"Could not log in to {address} ({err})", "Run 'Koble til e-post.command' again with a new app password.")
    except email_alerts.NotConfigured:
        info("finn.no / LinkedIn alerts: no inbox connected (optional). Run 'Koble til e-post.command' to add it.")

    r = subprocess.run(["launchctl", "print", f"gui/{subprocess.run(['id', '-u'], capture_output=True, text=True).stdout.strip()}/{LABEL}"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok("Automatic updates are on (every 2 hours)")
        if "last exit code = 0" not in r.stdout and "never exited" not in r.stdout:
            bad("The last automatic run ended with an error",
                "Open ~/Library/Logs/bachelor-finder.log and read the last lines.")
    else:
        info("Automatic updates are off. Double-click 'Slå på automatisk oppdatering.command' to turn them on.")

    page = env["root"] / "results.html"
    if page.exists():
        age_h = (time.time() - page.stat().st_mtime) / 3600
        if age_h > 48:
            bad(f"The results page is {age_h / 24:.0f} days old", "Double-click 'Finn bedrifter.command' to update it.")
        else:
            ok(f"The results page was updated {age_h:.0f} hours ago")
    else:
        info("No results page yet. Double-click 'Finn bedrifter.command'.")

    t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=env["root"],
                       capture_output=True, text=True)
    if t.returncode == 0:
        ok("Built-in tests pass")
    else:
        bad("Built-in tests fail", "The code was changed in a way that broke something. Details:\n" +
            "\n".join("             " + l for l in t.stderr.strip().splitlines()[-8:]))

    print("\n" + ("Alt ser bra ut." if not problems else f"{problems} problem(er) funnet, se over."))
    if problems:
        sys.exit(1)
