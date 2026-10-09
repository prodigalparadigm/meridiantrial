"""Channel normalizers: email, web form and call-center note -> one complaint shape.

Metadata (sender, phone, caller name) is used locally to find the customer and
to seed redaction. It is never sent to a model; only the redacted body is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .redact import _digits

CHANNEL_LABEL = {"email": "Email", "web_form": "Web form", "call_note": "Call-center note"}


@dataclass
class Complaint:
    id: str
    channel: str
    received_at: str
    text: str                                   # what gets analyzed (after redaction)
    sender: dict = field(default_factory=dict)  # local only
    customer: dict | None = None                # CRM match, local only
    known: dict = field(default_factory=dict)   # seeds for redaction


def _parse_from(s: str) -> tuple[str, str]:
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', s or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", (s or "").strip())


class CustomerDirectory:
    def __init__(self, crm: dict | None):
        self.customers = (crm or {}).get("customers", [])
        self._by_email = {c["email"].lower(): c for c in self.customers if c.get("email")}
        self._by_phone = {_digits(c["phone"])[-7:]: c for c in self.customers if c.get("phone")}
        self._by_name = {c["name"].lower(): c for c in self.customers if c.get("name")}

    def match(self, name: str = "", email: str = "", phone: str = "") -> dict | None:
        return (self._by_email.get((email or "").lower())
                or (self._by_phone.get(_digits(phone)[-7:]) if len(_digits(phone)) >= 7 else None)
                or self._by_name.get((name or "").strip().lower()))

    def names(self) -> list[str]:
        return [c["name"] for c in self.customers if c.get("name")]


def normalize(raw: dict, directory: CustomerDirectory) -> Complaint:
    ch, p = raw["channel"], raw["payload"]
    if ch == "email":
        name, email = _parse_from(p.get("from", ""))
        sender = {"name": name, "email": email}
        text = f"Subject: {p.get('subject', '').strip()}\n\n{p.get('body', '').strip()}"
    elif ch == "web_form":
        sender = {"name": p.get("name", ""), "email": p.get("email", ""), "phone": p.get("phone", ""),
                  "account_last4": p.get("account_last4", "")}
        text = f"Topic the customer selected: {p.get('topic_selected', 'Other')}\n\n{p.get('message', '').strip()}"
    elif ch == "call_note":
        sender = {"name": p.get("caller_name", ""), "phone": p.get("callback", ""), "agent_id": p.get("agent_id", "")}
        text = f"Note written by a contact-center representative after a call:\n\n{p.get('note', '').strip()}"
    else:
        raise ValueError(f"unknown channel {ch}")

    customer = directory.match(sender.get("name", ""), sender.get("email", ""), sender.get("phone", ""))
    known = {
        "names": [n for n in {sender.get("name", ""), (customer or {}).get("name", "")} if n],
        "emails": [e for e in {sender.get("email", ""), (customer or {}).get("email", "")} if e],
        "phones": [x for x in {sender.get("phone", ""), (customer or {}).get("phone", "")} if x],
        "accounts": [a["number"] for a in (customer or {}).get("accounts", [])] + ([sender["account_last4"]] if sender.get("account_last4") else []),
        "addresses": [(customer or {}).get("address", "")] if customer else [],
        "dobs": [(customer or {}).get("dob", "")] if customer else [],
    }
    return Complaint(id=raw["id"], channel=ch, received_at=raw["received_at"], text=text,
                     sender=sender, customer=customer, known=known)
