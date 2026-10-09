"""Router: which department owns the underlying problem."""
from __future__ import annotations

from .. import config
from .common import UNTRUSTED_FRAME, complaint_block

AGENT = "router"
EFFORT = "low"


def system_prompt() -> str:
    depts = "\n".join(f"- {d['id']} ({d['name']}): {d['owns']}" for d in config.departments())
    return f"""\
You route complaints at {config.BANK_NAME} to the department that owns the underlying problem, meaning the team that \
can actually investigate and fix it. That is often not the topic the customer picked on the web form, and not the \
place where the customer noticed the problem. For example, an unrecognized charge on a credit card belongs to Fraud & \
Disputes rather than Card Services, and a rude employee belongs to Branch & Contact Center even when the visit was \
about a loan.

If the complaint raises two separate problems owned by different teams, give the primary owner (the more serious \
or time-sensitive problem) and a secondary; otherwise the secondary is "none".

Departments (use only these ids):
{depts}

{UNTRUSTED_FRAME}"""


def schema() -> dict:
    ids = config.dept_ids()
    return {
        "type": "object",
        "properties": {
            "primary": {"type": "string", "enum": ids},
            "secondary": {"type": "string", "enum": ids + ["none"]},
            "rationale": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "injection_suspected": {"type": "boolean"},
        },
        "required": ["primary", "secondary", "rationale", "confidence", "injection_suspected"],
        "additionalProperties": False,
    }


def run(llm, case_id: str, redacted_text: str, channel_label: str):
    return llm.call(case_id=case_id, agent=AGENT, system=system_prompt(),
                    user=complaint_block(redacted_text, channel_label), schema=schema(), effort=EFFORT)
