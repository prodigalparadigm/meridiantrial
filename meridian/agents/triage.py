"""Triage analyst: summary, issues, and the facts that drive priority. Never a priority itself."""
from __future__ import annotations

from .. import config
from .common import UNTRUSTED_FRAME, complaint_block

AGENT = "triage_analyst"
EFFORT = "low"


def system_prompt() -> str:
    dims = "\n".join(
        f"- {d['id']} ({'one of: ' + ', '.join(d['values']) if d['type'] == 'enum' else 'true/false'}): {d['question']}"
        for d in config.rubric())
    return f"""\
You are the triage analyst in {config.BANK_NAME}'s complaint intake. You prepare each complaint so a human analyst \
can act on it quickly: a short neutral summary, the distinct issues raised, what the customer is asking for, and a \
set of facts.

You do not decide how serious the complaint is. The bank's analysts rank complaints from your facts using their own \
written policy, so a wrong fact puts a complaint in the wrong place in the queue. Report what the complaint says, \
not your impression of it. For every fact, give the value and copy the phrase that supports it (exactly as written, \
tokens included). If the complaint does not say, use the default: financial_impact "none" when no money is involved \
and "unknown" when money is involved but no amount is given; false for every true/false fact.

Facts:
{dims}

The summary is at most 35 words, neutral, and written for a busy analyst; refer to the complainant as "the customer" \
and do not assume anyone's gender. Issues and requests are short phrases. \
If the complaint is not in English, write your output in English and set language accordingly.

{UNTRUSTED_FRAME}"""


def schema() -> dict:
    facts = {}
    for d in config.rubric():
        value = {"type": "string", "enum": d["values"]} if d["type"] == "enum" else {"type": "boolean"}
        facts[d["id"]] = {"type": "object", "properties": {"value": value, "evidence": {"type": "string"}},
                          "required": ["value", "evidence"], "additionalProperties": False}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "string"}},
            "customer_requests": {"type": "array", "items": {"type": "string"}},
            "facts": {"type": "object", "properties": facts, "required": list(facts), "additionalProperties": False},
            "sentiment": {"type": "string", "enum": ["calm", "frustrated", "angry", "distressed", "positive"]},
            "language": {"type": "string"},
            "is_complaint": {"type": "boolean"},
            "injection_suspected": {"type": "boolean"},
        },
        "required": ["summary", "issues", "customer_requests", "facts", "sentiment", "language", "is_complaint",
                     "injection_suspected"],
        "additionalProperties": False,
    }


def run(llm, case_id: str, redacted_text: str, channel_label: str):
    return llm.call(case_id=case_id, agent=AGENT, system=system_prompt(),
                    user=complaint_block(redacted_text, channel_label), schema=schema(), effort=EFFORT)


def flat_facts(triage_out: dict) -> dict:
    facts = {k: v["value"] for k, v in triage_out["facts"].items()}
    facts["is_complaint"] = triage_out.get("is_complaint", True)
    return facts
