"""Egress gate: the single choke point between bank data and any model.

Every outbound payload passes through `EgressGate.check` inside llm.py; there is
no other code path to the network. If the gate finds anything that looks like
PII outside a redaction token, the call is not made, the complaint fails closed
to manual triage, and the block is logged. Nothing about the gate is
probabilistic.

What it guards against: a code path that skips redaction, a redaction bug that
leaves a pattern behind, and any known customer identifier (from the full CRM
directory) appearing anywhere in the payload. What it cannot catch: a name the
redactor missed that is in no directory and has no pattern; that residual is
measured in evals/, not assumed away.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .redact import (ADDRESS_RE, DIGIT_RUN_RE, EMAIL_OBFUSCATED_RE, EMAIL_RE, HONORIFIC_RE, PHONE_RE, SSN_CUE_RE,
                     SSN_RE, TOKEN_RE, _LET, _digits, _looks_like_amount, _looks_like_date_or_time, _luhn_ok)


@dataclass
class GateResult:
    allowed: bool
    findings: list[dict] = field(default_factory=list)   # kind + detector only; never the value


class EgressBlocked(Exception):
    def __init__(self, findings):
        super().__init__(f"egress blocked: {sorted({f['kind'] for f in findings})}")
        self.findings = findings


class EgressGate:
    def __init__(self, directory: list[dict] | None = None):
        directory = directory or []
        self.names = sorted({c["name"] for c in directory if c.get("name")}, key=len, reverse=True)
        self.emails = {c["email"].lower() for c in directory if c.get("email")}
        self.phones = {_digits(c["phone"])[-7:] for c in directory if c.get("phone") and len(_digits(c["phone"])) >= 7}
        self.accounts = {_digits(a["number"]) for c in directory for a in c.get("accounts", []) if len(_digits(a.get("number", ""))) >= 6}
        self._name_re = re.compile("|".join(rf"(?<!{_LET}){re.escape(n)}(?!{_LET})" for n in self.names), re.I) if self.names else None

    def check(self, *parts: str) -> GateResult:
        text = TOKEN_RE.sub(" ", "\n".join(parts))
        f: list[dict] = []
        if EMAIL_RE.search(text):
            f.append({"kind": "EMAIL", "detector": "pattern"})
        for m in EMAIL_OBFUSCATED_RE.finditer(text):
            if re.search(r"\b(?:dot|com|net|org)\b", m.group(0), re.I):
                f.append({"kind": "EMAIL", "detector": "obfuscated"})
        if SSN_RE.search(text) or SSN_CUE_RE.search(text):
            f.append({"kind": "SSN", "detector": "pattern"})
        if PHONE_RE.search(text):
            f.append({"kind": "PHONE", "detector": "pattern"})
        for m in DIGIT_RUN_RE.finditer(text):
            d = _digits(m.group(0))
            if 13 <= len(d) <= 19 and _luhn_ok(d):
                f.append({"kind": "CARD", "detector": "luhn"})
            elif len(d) >= 6 and not _looks_like_date_or_time(m.group(0)) and not _looks_like_amount(m.group(0)):
                f.append({"kind": "ACCOUNT", "detector": "digit_run"})
            if d in self.accounts or (len(d) >= 7 and d[-7:] in self.phones):
                f.append({"kind": "KNOWN_IDENTIFIER", "detector": "directory"})
        if ADDRESS_RE.search(text):
            f.append({"kind": "ADDRESS", "detector": "pattern"})
        if HONORIFIC_RE.search(text):
            f.append({"kind": "NAME", "detector": "honorific"})
        if self._name_re and self._name_re.search(text):
            f.append({"kind": "CUSTOMER_NAME", "detector": "directory"})
        low = text.lower()
        if any(e in low for e in self.emails):
            f.append({"kind": "EMAIL", "detector": "directory"})
        return GateResult(allowed=not f, findings=f)

    def enforce(self, *parts: str) -> None:
        res = self.check(*parts)
        if not res.allowed:
            raise EgressBlocked(res.findings)
