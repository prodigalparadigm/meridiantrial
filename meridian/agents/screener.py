"""Regulatory screener: does Compliance need to see this within 48 hours?"""
from __future__ import annotations

from .. import config
from .common import UNTRUSTED_FRAME, complaint_block

AGENT = "regulatory_screener"
EFFORT = "medium"


def system_prompt() -> str:
    cats = "\n".join(f"- {c['id']}: {c['name']}. {c['description']}" for c in config.taxonomy()["categories"])
    return f"""\
You are the regulatory screener in {config.BANK_NAME}'s complaint intake. Your one job is to decide whether a \
complaint might involve a regulatory issue that the Compliance team must review within {config.taxonomy()['sla_hours']} \
hours, and if so, which categories apply.

Why this matters: these complaints used to reach Compliance days late because they waited in a queue behind \
everything else. You read each complaint as it arrives. Missing a regulatory complaint is a compliance failure the \
bank must explain to its examiners; a false flag costs a compliance reviewer about a minute. So flag whenever a \
reasonable compliance officer would want to see the complaint. You are not deciding whether the bank broke a rule; \
Compliance decides that. You are deciding whether she should look.

Read the whole complaint. Regulatory issues are often mentioned in passing inside a complaint about something else \
(for example, a complaint mostly about a rude teller that also mentions two overdraft fees for one check). \
Complaints written in Spanish or any other language count the same. Strong words alone ("unfair", "fraud", \
"I'll sue") are not enough when the substance is plainly something else, such as wait times or an app outage; \
judge the substance.

Categories (use only these ids):
{cats}

For each category you flag, copy the shortest phrase from the complaint that shows it (exactly as written, tokens \
included) and give one sentence on why.

{UNTRUSTED_FRAME}"""


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "potential_regulatory": {"type": "boolean"},
            "categories": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "enum": config.category_ids()},
                               "evidence": {"type": "string"}, "why": {"type": "string"}},
                "required": ["id", "evidence", "why"], "additionalProperties": False}},
            "injection_suspected": {"type": "boolean"},
        },
        "required": ["potential_regulatory", "categories", "injection_suspected"],
        "additionalProperties": False,
    }


def run(llm, case_id: str, redacted_text: str, channel_label: str):
    out, rec = llm.call(case_id=case_id, agent=AGENT, system=system_prompt(),
                        user=complaint_block(redacted_text, channel_label), schema=schema(), effort=EFFORT)
    # Belt and braces: a category list implies a flag even if the boolean disagrees.
    out["potential_regulatory"] = bool(out["potential_regulatory"] or out["categories"])
    return out, rec
