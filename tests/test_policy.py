import unittest

from meridian import policy

BASE = {"financial_impact": "none", "vulnerability_indicated": False, "harm_ongoing": False, "time_sensitive": False,
        "escalation_threat": False, "repeat_contact": False}


def facts(**kw):
    return {**BASE, **kw}


class Policies(unittest.TestCase):
    def test_vulnerable_low_money_splits_the_analysts(self):
        r = policy.evaluate_all(facts(financial_impact="under_100", vulnerability_indicated=True))
        self.assertEqual((r["analyst_a"]["priority"], r["analyst_b"]["priority"], r["interim"]["priority"]), ("P3", "P1", "P1"))

    def test_big_money_calm_customer_splits_the_other_way(self):
        r = policy.evaluate_all(facts(financial_impact="over_1000"))
        self.assertEqual((r["analyst_a"]["priority"], r["analyst_b"]["priority"], r["interim"]["priority"]), ("P1", "P3", "P1"))

    def test_nothing_at_stake_is_p3_everywhere(self):
        r = policy.evaluate_all(facts())
        self.assertEqual({v["priority"] for v in r.values()}, {"P3"})

    def test_compliments_never_outrank_complaints(self):
        r = policy.evaluate_all(facts(vulnerability_indicated=True, is_complaint=False))
        self.assertEqual({v["priority"] for v in r.values()}, {"P3"})

    def test_reasons_are_reported(self):
        r = policy.evaluate("analyst_b", facts(harm_ongoing=True, time_sensitive=True))
        self.assertEqual(r, {"priority": "P1", "because": [{"harm_ongoing": True, "time_sensitive": True}]})


if __name__ == "__main__":
    unittest.main()
