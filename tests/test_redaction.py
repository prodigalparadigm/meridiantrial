import re
import unittest
from unittest import mock

from meridian import redact
from meridian.redact import Redactor

R = Redactor(directory_names=["Thaddeus Abernathy-Kwan"])


def survives(secret: str, text: str) -> bool:
    """True if any part of the secret is still readable: the whole string, or any 3+ digit group of a number."""
    groups = [g for g in re.split(r"\D+", secret) if len(g) >= 3]
    if groups:
        return any(g in re.sub(r"⟦[^⟧]*⟧", " ", text) for g in groups)
    return secret in text
KNOWN = {"names": ["Margaret Okafor"], "emails": ["m.okafor@example.com"], "phones": ["845-555-0142"],
         "accounts": ["441022998812"]}

# (fixture text, the substring that must disappear, the detector that should catch it)
FIXTURES = [
    ("Email me at jane.q@example.org please", "jane.q@example.org", "EMAIL"),
    ("write to jsmith at example dot com", "jsmith at example dot com", "EMAIL"),
    ("call (845) 555-0199 after 5", "555-0199", "PHONE"),
    ("my number is five five five, oh one two two, thanks", "five five five, oh one two two", "PHONE"),
    ("SSN 900-12-3456 on file", "900-12-3456", "SSN"),
    ("my social is 900123456", "900123456", "SSN"),
    ("card 4000 0012 3456 7899 was declined", "4000 0012 3456 7899", "CARD"),
    ("card 4000 0012\n3456 7899 was declined", "4000 0012\n3456 7899", "CARD"),
    ("routing 021000021 and", "021000021", "ROUTING"),
    ("acct 5512-0098-77 is mine", "5512-0098-77", "ACCOUNT"),
    ("the card ending in 4821 was charged", "4821", "ACCOUNT"),
    ("DOB 02/11/1946 per your form", "02/11/1946", "DOB"),
    ("I live at 22 N. Hollow Brook Lane, Apt 3, Pine Plains NY 12567 now", "22 N. Hollow Brook Lane", "ADDRESS"),
    ("I spoke with Mr. Delacroix at the desk", "Delacroix", "NAME"),
    ("the teller, Priya, was rude", "Priya", "NAME"),
    ("my son Dmitri handles my bills", "Dmitri", "NAME"),
    ("then Oluwaseun told me to leave", "Oluwaseun", "NAME"),
    ("Thanks,\nBeatrix Vandermolen\n", "Beatrix Vandermolen", "NAME"),
    ("Mi nombre es Lucía Hernández y estoy molesta", "Lucía", "NAME"),
    ("CUST SAYS SON DMITRI HAS POA", "DMITRI", "NAME"),
]


class RedactionFixtures(unittest.TestCase):
    def test_each_fixture_is_removed_by_its_detector(self):
        self.assertGreater(len(FIXTURES), 15)
        for text, secret, detector in FIXTURES:
            with self.subTest(secret=secret):
                red = R.redact(text, {})
                self.assertFalse(survives(secret, red.text), red.text)
                self.assertIn(detector, {f["source"] for f in red.found} | {f["kind"] for f in red.found})

    def test_known_entities_replaced_with_customer_tokens(self):
        red = R.redact("Margaret Okafor here, acct 441022998812, m.okafor@example.com, 845-555-0142. -- Margaret", KNOWN)
        for s in ("Margaret", "Okafor", "441022998812", "m.okafor@example.com", "555-0142"):
            self.assertNotIn(s, red.text)
        self.assertIn("⟦CUSTOMER_NAME_1⟧", red.text)

    def test_directory_names_are_redacted_even_without_metadata(self):
        red = R.redact("my husband thaddeus abernathy-kwan opened it", {})
        self.assertNotIn("abernathy", red.text.lower())

    def test_amounts_dates_and_places_survive(self):
        text = "On 9/3/2026 you charged $1,250.00 and 1250.00 again at the Route 9 branch on Monday; reply in 10 business days via Zelle."
        red = R.redact(text, {})
        self.assertEqual(text, red.text, red.found)

    def test_rehydrate_round_trip(self):
        text = "Margaret Okafor, card 4000 0012 3456 7899, my son Dmitri, SSN 900-12-3456."
        red = R.redact(text, KNOWN)
        self.assertEqual(text, red.rehydrate(red.text))

    def test_same_value_gets_same_token(self):
        red = R.redact("Dmitri said so. Later Dmitri called.", {})
        self.assertEqual(len(set(re.findall(r"⟦NAME_\d+⟧", red.text))), 1)


class Erosion(unittest.TestCase):
    """Switch off a detector: its fixture must then leak. Proves the fixtures test something."""

    NEVER = re.compile(r"(?!x)x")

    def _leaks(self, text, secret, **patches):
        with mock.patch.multiple(redact, **patches):
            return survives(secret, Redactor().redact(text, {}).text)

    def test_ssn_detector_is_load_bearing(self):
        self.assertTrue(self._leaks("SSN 900-12-3456 on file", "900-12-3456", SSN_RE=self.NEVER, SSN_CUE_RE=self.NEVER,
                                    PHONE_RE=self.NEVER, DIGIT_RUN_RE=self.NEVER))

    def test_email_detector_is_load_bearing(self):
        self.assertTrue(self._leaks("Email me at jane.q@example.org please", "jane.q@example.org", EMAIL_RE=self.NEVER))

    def test_cue_detector_is_load_bearing(self):
        # "Priya" is in the first-name list too; remove both layers to see the leak.
        with mock.patch.object(redact, "COMMON_FIRST_NAMES", frozenset()):
            self.assertTrue(self._leaks("the teller, Priya, was rude", "Priya", CUE_NAME_RE=self.NEVER,
                                        CAP_SEQ_RE=self.NEVER))

    def test_luhn_card_detector_is_load_bearing(self):
        self.assertTrue(self._leaks("4000 0012 3456 7899 was declined", "4000 0012 3456 7899", DIGIT_RUN_RE=self.NEVER))


if __name__ == "__main__":
    unittest.main()
