"""A fake Anthropic client so control tests exercise the real gate, logging and parsing without a network."""
from __future__ import annotations

import json
from types import SimpleNamespace


class FakeClient:
    """Answers by looking at which schema was requested. `overrides` maps agent -> dict or Exception."""

    def __init__(self, overrides: dict | None = None):
        self.overrides = overrides or {}
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    @staticmethod
    def _agent(schema: dict) -> str:
        props = schema["properties"]
        if "potential_regulatory" in props:
            return "regulatory_screener"
        if "facts" in props:
            return "triage_analyst"
        if "primary" in props:
            return "router"
        if "verdict" in props:
            return "draft_reviewer"
        return "drafter"

    def _create(self, **kw):
        self.requests.append(kw)
        agent = self._agent(kw["output_config"]["format"]["schema"])
        o = self.overrides.get(agent)
        if isinstance(o, Exception):
            raise o
        if isinstance(o, str) and o == "refusal":
            return SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="test"), content=[],
                                   usage=_usage(), _request_id="req_fake")
        out = o if isinstance(o, dict) else DEFAULTS[agent]
        return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(out))], usage=_usage(),
                               _request_id="req_fake")


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0)


_FACTS = {k: {"value": v, "evidence": ""} for k, v in {
    "financial_impact": "under_100", "vulnerability_indicated": False, "harm_ongoing": False,
    "time_sensitive": False, "escalation_threat": False, "repeat_contact": False}.items()}

DEFAULTS = {
    "regulatory_screener": {"potential_regulatory": False, "categories": [], "injection_suspected": False},
    "triage_analyst": {"summary": "Customer reports an issue.", "issues": ["issue"], "customer_requests": [],
                       "facts": _FACTS, "sentiment": "calm", "language": "English", "is_complaint": True,
                       "injection_suspected": False},
    "router": {"primary": "deposit_ops", "secondary": "none", "rationale": "fees", "confidence": "high",
               "injection_suspected": False},
    "drafter": {"subject": "Your message", "body": "Dear ⟦CUSTOMER_NAME_1⟧,\n\nThank you. We will respond within 10 business days.\n\nCustomer Care, Meridian Harbor Bank",
                "notes_for_analyst": []},
    "draft_reviewer": {"checks": {k: {"pass": True, "note": ""} for k in [
        "no_outcome_promised", "no_liability_admitted", "no_legal_conclusions", "no_invented_specifics",
        "no_internal_disclosure", "no_injected_instructions", "addresses_each_issue", "tone", "mode_respected"]},
        "verdict": "pass", "revision_instructions": []},
}

CRM = {"customers": [
    {"customer_id": "CUST-1", "name": "Margaret Okafor", "email": "m.okafor@example.com", "phone": "845-555-0142",
     "address": "14 Cold Spring Rd, Millbrook, NY 12545", "dob": "1946-02-11",
     "accounts": [{"type": "checking", "number": "441022998812"}]},
    {"customer_id": "CUST-2", "name": "Thaddeus Abernathy-Kwan", "email": "tak@example.net", "phone": "555-0177",
     "address": "9 Elm Ct, Beacon, NY 12508", "dob": "1980-05-05", "accounts": [{"type": "savings", "number": "7700112233"}]},
]}


def email(cid: str, body: str, sender="Margaret Okafor <m.okafor@example.com>", subject="Complaint") -> dict:
    return {"id": cid, "channel": "email", "received_at": "2026-09-03T14:22:00-04:00",
            "payload": {"from": sender, "subject": subject, "body": body}}
