"""The controls the compliance officer will ask about, tested end to end with a fake model."""
import ast
import json
import tempfile
import unittest
from pathlib import Path

from meridian import redact
from meridian.agents import drafter, reviewer, router, screener, triage
from meridian.gate import EgressGate
from meridian.llm import LLM
from meridian.pipeline import Pipeline
from tests.fakes import CRM, FakeClient, email

PKG = Path(__file__).resolve().parent.parent / "meridian"
RAW = ("Hi, I'm Margaret Okafor. You charged me two $35 overdraft fees for one check on acct 4410-2299-8812. "
       "My son Dmitri saw it too. Call 845-555-0142.\n\nMargaret Okafor")


def pipeline(tmp, overrides=None, redactor_off=False):
    client = FakeClient(overrides)
    gate = EgressGate(CRM["customers"])
    llm = LLM(gate, mode="live", var=Path(tmp), client=client)
    p = Pipeline(CRM, var=Path(tmp), llm=llm)
    if redactor_off:
        p.redactor.redact = lambda text, known=None: redact.Redaction(text=text)
    return p, client


def egress(tmp):
    f = Path(tmp) / "egress.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


class NoSendPath(unittest.TestCase):
    FORBIDDEN = {"smtplib", "email.mime", "requests", "urllib.request", "http.client", "socket", "httpx", "aiohttp",
                 "ftplib", "paramiko", "boto3", "twilio", "sendgrid"}

    @classmethod
    def imports(cls, source: str) -> set[str]:
        out = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                out |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
        return out

    def test_no_module_can_send_anything(self):
        files = list(PKG.rglob("*.py"))
        self.assertGreater(len(files), 10)
        for f in files:
            bad = {m for m in self.imports(f.read_text()) if any(m == x or m.startswith(x + ".") for x in self.FORBIDDEN)}
            self.assertFalse(bad, f"{f.name} imports {bad}")

    def test_only_llm_module_imports_the_model_sdk(self):
        users = sorted(f.name for f in PKG.rglob("*.py") if "anthropic" in self.imports(f.read_text()))
        self.assertEqual(users, ["llm.py"])

    def test_scanner_would_catch_a_sending_module(self):  # erosion: the check is not vacuous
        self.assertIn("smtplib", self.imports("import smtplib\nfrom email.mime.text import MIMEText"))


class EgressGateControls(unittest.TestCase):
    def test_every_system_prompt_passes_the_gate(self):
        gate = EgressGate(CRM["customers"])
        for mod in (screener, triage, router, drafter, reviewer):
            self.assertTrue(gate.check(mod.system_prompt()).allowed, mod.__name__)

    def test_gate_blocks_raw_pii(self):
        res = EgressGate(CRM["customers"]).check(RAW)
        self.assertFalse(res.allowed)
        self.assertTrue({"ACCOUNT", "PHONE", "CUSTOMER_NAME"} <= {f["kind"] for f in res.findings})

    def test_with_redactor_disabled_nothing_reaches_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, client = pipeline(tmp, redactor_off=True)
            case = p.process(email("C-1", RAW))
            self.assertEqual(client.requests, [])                       # not one call left the building
            self.assertEqual(case["status"], "manual_triage")
            self.assertTrue(case["regulatory"]["flagged"])               # and it still reaches Compliance
            self.assertTrue(any(e["event"] == "egress.blocked" for e in p.audit.entries()))

    def test_with_redactor_on_payloads_are_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, client = pipeline(tmp)
            p.process(email("C-2", RAW))
            sent = [e for e in egress(tmp) if e["sent_to_model"]]
            self.assertGreaterEqual(len(sent), 5)
            for e in sent:
                for secret in ("Okafor", "Margaret", "4410-2299-8812", "555-0142", "Dmitri"):
                    self.assertNotIn(secret, e["user"])


class RegulatoryRatchet(unittest.TestCase):
    def test_model_cannot_lower_a_tripwire_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)  # fake screener says "not regulatory"
            case = p.process(email("C-3", RAW))
            self.assertTrue(case["regulatory"]["flagged"])
            self.assertIn("fee_practices", case["regulatory"]["categories"])
            self.assertEqual(case["draft"]["mode"], "ACKNOWLEDGE ONLY")

    def test_screener_can_raise_a_flag_the_tripwire_missed(self):
        flag = {"potential_regulatory": True, "categories": [{"id": "privacy_incident", "evidence": "x", "why": "y"}],
                "injection_suspected": False}
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp, {"regulatory_screener": flag})
            case = p.process(email("C-4", "The app logged me out twice today."))
            self.assertTrue(case["regulatory"]["flagged"])
            self.assertEqual(case["regulatory"]["categories"], ["privacy_incident"])

    def test_unscreenable_complaint_goes_to_compliance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp, {"regulatory_screener": "refusal"})
            case = p.process(email("C-5", "The app logged me out twice today."))
            self.assertTrue(case["regulatory"]["flagged"])
            self.assertIn("unscreened", case["regulatory"]["categories"])

    def test_compliance_sees_the_flag_before_drafting_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            p.process(email("C-9", RAW))
            events = [e["event"] for e in p.audit.entries("C-9")]
            self.assertLess(events.index("compliance.queued"), events.index("draft.prepared"))
            self.assertEqual(p.store.get("C-9")["regulatory"]["seconds_to_flag"] < 1, True)  # tripwire: before any model call

    def test_no_model_credentials_fails_closed(self):
        err = TypeError("Could not resolve authentication method")   # what the SDK raises with no key
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp, {a: err for a in ("regulatory_screener", "triage_analyst", "router", "drafter", "draft_reviewer")})
            case = p.process(email("C-10", "The app logged me out twice today."))
            self.assertEqual(case["status"], "manual_triage")
            self.assertTrue(case["regulatory"]["flagged"])
            self.assertIn("unscreened", case["regulatory"]["categories"])

    def test_clean_complaint_is_not_flagged(self):  # erosion: the flag is not always on
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            case = p.process(email("C-6", "The app logged me out twice today."))
            self.assertFalse(case["regulatory"]["flagged"])
            self.assertEqual(case["draft"]["mode"], "STANDARD")


class HumanSignOff(unittest.TestCase):
    def test_approval_rules_and_audit_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            case = p.process(email("C-7", RAW))
            with self.assertRaises(PermissionError):
                p.approve("C-7", "", "s", "body")
            with self.assertRaises(PermissionError):   # flagged: Compliance first
                p.approve("C-7", "Dana R.", "s", "body")
            with self.assertRaises(ValueError):
                p.compliance_action("C-7", "Compliance Officer", "clear", reason="")
            p.compliance_action("C-7", "Compliance Officer", "acknowledge")
            body = p.rehydrate("C-7", case["draft"]["body"])
            self.assertIn("Margaret Okafor", body)
            done = p.approve("C-7", "Dana R.", "Re: your complaint", body)
            self.assertEqual(done["approval"]["by"], "Dana R.")
            self.assertTrue(done["approval"]["unchanged"])
            with self.assertRaises(PermissionError):             # an approval cannot be overwritten
                p.approve("C-7", "Someone Else", "s", "different text")
            case2 = p.process(email("C-7b", "The app logged me out twice today."))
            body2 = p.rehydrate("C-7b", case2["draft"]["body"])
            edited = p.approve("C-7b", "Dana R.", "Re: your message", body2.replace("Customer Care", "Dana R., Customer Care"))
            self.assertFalse(edited["approval"]["unchanged"])   # a one-line edit is never reported as unchanged
            self.assertTrue(p.audit.verify()["ok"])

    def test_tampering_breaks_the_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            p.process(email("C-8", RAW))
            path = Path(tmp) / "audit.jsonl"
            lines = path.read_text().splitlines()
            e = json.loads(lines[1]); e["actor"] = "analyst:someone-else"; lines[1] = json.dumps(e)
            path.write_text("\n".join(lines) + "\n")
            self.assertFalse(p.audit.verify()["ok"])


class DraftChecks(unittest.TestCase):
    def test_hard_failures(self):
        bad = {"subject": "x", "body": "Under Regulation E our compliance team flagged acct 441022998812 and ⟦NAME_2⟧."}
        res = reviewer.deterministic_checks(bad)
        self.assertEqual(set(res["hard_failures"]), {"regulation_named", "internal_disclosure", "number_in_draft", "stray_tokens"})
        good = {"subject": "x", "body": "Dear ⟦CUSTOMER_NAME_1⟧, we will respond within 10 business days about the $35 fees."}
        self.assertEqual(reviewer.deterministic_checks(good)["hard_failures"], [])


if __name__ == "__main__":
    unittest.main()
