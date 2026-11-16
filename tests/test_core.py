"""Tests for the parts that break quietly. Run from the project folder:

    python3 -m unittest discover -s tests

No network is used. Dates are passed in explicitly so the tests do not depend on today.
"""
import datetime as dt
import email
import email.policy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import email_alerts  # noqa: E402
import finder  # noqa: E402
import listings  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
BACHELOR = {"nivaa": "bachelor", "ferdig": 2027}
NOV_2026 = dt.date(2026, 11, 16)


class Dates(unittest.TestCase):
    def test_formats(self):
        cases = {"01.12.2026": "2026-12-01", "1. desember": "2026-12-01", "Søknadsfrist: 15. januar 2027": "2027-01-15",
                 "December 1, 2026": "2026-12-01", "2026-11-30T00:00": "2026-11-30", "1/12": "2026-12-01",
                 "Apply by 5th of December": "2026-12-05"}
        for text, want in cases.items():
            self.assertEqual(listings.parse_date(text, NOV_2026).isoformat(), want, text)

    def test_not_a_date(self):
        self.assertIsNone(listings.parse_date("3 markeder", NOV_2026))
        self.assertIsNone(listings.parse_date("Snarest", NOV_2026))

    def test_year_is_inferred_from_today_not_hardcoded(self):
        self.assertEqual(listings.parse_date("1. mars", NOV_2026).isoformat(), "2027-03-01")
        self.assertEqual(listings.parse_date("1. mars", dt.date(2028, 11, 16)).isoformat(), "2029-03-01")

    def test_rolling_and_text_deadlines(self):
        self.assertEqual(listings.deadline_from_field("Snarest", NOV_2026)["kind"], "rolling")
        d = listings.deadline_in_text("Vi tar inn studenter. Søknadsfrist er 1. desember. Velkommen", NOV_2026)
        self.assertEqual((d["date"], d["kind"]), ("2026-12-01", "guess"))

    def test_next_summer_rolls_over(self):
        self.assertEqual(listings.next_summer(dt.date(2026, 11, 16)), 2027)
        self.assertEqual(listings.next_summer(dt.date(2027, 3, 1)), 2027)
        self.assertEqual(listings.next_summer(dt.date(2028, 9, 1)), 2029)


class Eligibility(unittest.TestCase):
    def verdict(self, title, text, kind="summer", profile=BACHELOR):
        return listings.eligible(profile, title, text, kind)["verdict"]

    def test_master_only_is_hidden_for_bachelor(self):
        self.assertEqual(self.verdict("Summer Internship", "Vi søker masterstudenter innen informatikk."), "no")
        self.assertEqual(self.verdict("Sommerjobb utvikler", "For deg som går 4. eller 5. året på sivilingeniørstudiet."), "no")

    def test_master_only_is_fine_for_master_student(self):
        self.assertEqual(self.verdict("Summer Internship", "Vi søker masterstudenter innen informatikk.",
                                      profile={"nivaa": "master", "ferdig": 2029}), "ok")

    def test_bachelor_or_master_is_ok(self):
        self.assertEqual(self.verdict("Summer Internship", "Du studerer bachelor eller master innen IT."), "ok")
        self.assertEqual(self.verdict("Graduate Program", "Du er nyutdannet med bachelor- eller mastergrad.", "graduate"), "ok")

    def test_other_reasons_to_hide(self):
        self.assertEqual(self.verdict("Trainee jus og økonomi", "Vi søker trainee.", "graduate"), "no")
        self.assertEqual(self.verdict("Junior utvikler (graduate)", "Minimum 3 års erfaring med Java.", "graduate"), "no")
        self.assertEqual(self.verdict("Se tilgjengelige masteroppgaver", "Masteroppgaver for studenter.", "thesis"), "no")

    def test_conditions_we_cannot_know_are_flagged(self):
        self.assertEqual(self.verdict("Sommerjobb kryptologi", "Krever sikkerhetsklarering. Studenter innen informatikk."), "check")
        self.assertEqual(self.verdict("Tech Summer Internship", "You have one year left of your studies."), "check")

    def test_silent_ad_is_shown(self):
        self.assertEqual(self.verdict("Sommerjobb", "Vi tar inn sommerstudenter hvert år."), "ok")

    def test_field_flag_ignores_english_it(self):
        self.assertFalse(listings.eligible(BACHELOR, "Summer job", "it is a great place to work", "summer")["field"])
        self.assertTrue(listings.eligible(BACHELOR, "Summer job", "You study IT or computer science", "summer")["field"])


class Classification(unittest.TestCase):
    def setUp(self):
        self.clf = listings.Classifier(CFG, finder.kw_regex)

    def test_types(self):
        year = dt.date.today().year + 1
        self.assertEqual(self.clf.type_of(f"Sommerjobb {year} - utvikler"), "summer")
        self.assertEqual(self.clf.type_of("Software Engineer Intern"), "intern")
        self.assertEqual(self.clf.type_of("Graduate Program"), "graduate")
        self.assertEqual(self.clf.type_of("Senior backend developer"), "")

    def test_other_professions_and_old_ads_are_dropped(self):
        self.assertEqual(self.clf.type_of("Sommervikar sykepleier"), "")
        self.assertEqual(self.clf.type_of(f"Summer internship {dt.date.today().year - 1}"), "")

    def test_norwegian_intern_means_internal(self):
        self.assertEqual(self.clf.type_of("Intern IT-ansvarlig"), "")


def _mail(sender, plain="", html=""):
    msg = email.message.EmailMessage(policy=email.policy.default)
    msg["From"], msg["Date"], msg["Subject"] = sender, "Mon, 16 Nov 2026 08:00:00 +0100", "alert"
    msg.set_content(plain or "see html")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


class AlertEmails(unittest.TestCase):
    """Constructed samples in the known layout of these emails (no real alert was available
    when this was written). If a real alert parses to nothing, add it here and fix the parser."""

    def test_linkedin_plain_text(self):
        plain = ("Your job alert for intern in Oslo\n\nSoftware Engineer Intern\nArdoq\nOslo, Norway\n"
                 "View job: https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc\n\n"
                 "---------\nData Science Summer Intern\nDNB\nOslo, Norway\n"
                 "View job: https://www.linkedin.com/comm/jobs/view/4099999999?refId=x\n")
        items = email_alerts.parse_message(_mail("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", plain))
        self.assertEqual([(i["title"], i["employer"], i["url"]) for i in items],
                         [("Software Engineer Intern", "Ardoq", "https://www.linkedin.com/jobs/view/4012345678/"),
                          ("Data Science Summer Intern", "DNB", "https://www.linkedin.com/jobs/view/4099999999/")])
        self.assertEqual(items[0]["published"], "2026-11-16")

    def test_linkedin_html_only(self):
        html = ('<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?t=1">Software Engineer Intern</a>'
                '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?t=2">Ardoq · Oslo</a>'
                '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?t=3">View job</a>')
        items = email_alerts.parse_message(_mail("jobs-noreply@linkedin.com", "", html))
        self.assertEqual((items[0]["title"], items[0]["employer"], items[0]["location"]),
                         ("Software Engineer Intern", "Ardoq", "Oslo"))

    def test_finn_html(self):
        html = ('<a href="https://www.finn.no/job/fulltime/ad.html?finnkode=391234567"><img></a>'
                '<a href="https://www.finn.no/job/fulltime/ad.html?finnkode=391234567">Sommerjobb for IT-studenter</a>'
                '<a href="https://www.finn.no/391234999">Se annonsen</a>'
                '<a href="https://www.finn.no/job/ad/391234888">Summer Internship Tech</a>')
        items = email_alerts.parse_message(_mail("FINN.no <noreply@finn.no>", "", html))
        self.assertEqual({(i["title"], i["url"]) for i in items},
                         {("Sommerjobb for IT-studenter", "https://www.finn.no/391234567"),
                          ("Summer Internship Tech", "https://www.finn.no/391234888")})

    def test_other_senders_are_ignored(self):
        self.assertEqual(email_alerts.parse_message(_mail("someone@example.com", "https://www.finn.no/391234567")), [])


class ProjectFiles(unittest.TestCase):
    def test_config_has_what_the_code_reads(self):
        for key in ("registry", "crawl", "scoring", "tags", "ideas", "listings", "profil", "gruppe"):
            self.assertIn(key, CFG)
        for key in ("days_back", "areas", "types", "it_title", "it_strong"):
            self.assertIn(key, CFG["listings"])

    def test_template_placeholders_exist(self):
        tpl = (ROOT / "template.html").read_text(encoding="utf-8")
        self.assertEqual(tpl.count("/*__DATA__*/null"), 1)
        self.assertEqual(tpl.count('/*__BOOKMARKLET__*/""'), 1)


if __name__ == "__main__":
    unittest.main()
