"""Local PII redaction. Runs on bank hardware before any text can reach a model.

Layers, in order of reliability:
  1. Known entities: the complainant's own name, email, phone, address and
     account numbers from channel metadata and the CRM record, plus the full
     name of every customer in the directory.
  2. Patterns: email, phone, SSN, card (Luhn), routing, account-like digit
     runs, dates of birth, street addresses, obfuscated emails/phones.
  3. Name heuristics: honorifics, role/relationship cues ("my son X",
     "the teller, X"), sign-offs, a first-name list, and capitalized
     mid-sentence words that are not English words.

Layer 3 is the weakest and is measured, not trusted: see evals/.
Every original value is swapped for a stable token like ⟦NAME_2⟧; the
token -> value vault never leaves this process except to local storage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .names import AMBIGUOUS_FIRST_NAMES, COMMON_FIRST_NAMES, KEEP_CAPITALIZED

TOKEN_RE = re.compile(r"⟦[A-Z_]+_\d+⟧")

# Higher wins when spans overlap.
_PRIORITY = {"KNOWN": 100, "CARD": 90, "SSN": 85, "EMAIL": 80, "ROUTING": 75,
             "PHONE": 72, "ACCOUNT": 70, "DOB": 60, "ADDRESS": 55, "NAME": 40}


@dataclass
class Span:
    start: int
    end: int
    kind: str          # token family, e.g. NAME, ACCOUNT
    source: str        # which detector fired
    priority: int


@dataclass
class Redaction:
    text: str
    vault: dict[str, str] = field(default_factory=dict)          # token -> original
    found: list[dict] = field(default_factory=list)              # {token, kind, source}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.found:
            out[f["kind"]] = out.get(f["kind"], 0) + 1
        return out

    def rehydrate(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.vault.get(m.group(0), m.group(0)), text)


# --------------------------------------------------------------------- words
_DICT_PATHS = ["/usr/share/dict/words", "/usr/dict/words"]


def _load_english() -> set[str]:
    for p in _DICT_PATHS:
        path = Path(p)
        if path.exists():
            # Lowercase entries only: proper nouns in the list are capitalized,
            # so "Kevin" is excluded while "rose" and "price" are kept.
            return {w.strip() for w in path.read_text(errors="ignore").splitlines()
                    if w.strip() and w.strip()[0].islower()}
    # No system dictionary: an empty set means every unknown capitalized word
    # is treated as a possible name. Over-redaction is the safe failure.
    return set()


ENGLISH = _load_english()
_IRREGULAR = set("""has had does did done goes went gone said told paid made took taken gave given got gotten been being am are
was were is isn't wasn't weren't don't doesn't didn't can't won't wouldn't couldn't shouldn't haven't hasn't hadn't i'm
i've i'd i'll you're we're they're it's that's there's what's let's ain't y'all children people""".split())


def is_english(word: str) -> bool:
    w = _base(word).lower()
    if not w or w in ENGLISH or w in _IRREGULAR:
        return True
    stems = []
    if w.endswith("ies"): stems.append(w[:-3] + "y")
    if w.endswith("ied"): stems.append(w[:-3] + "y")
    if w.endswith("es"): stems.append(w[:-2])
    if w.endswith("s"): stems.append(w[:-1])
    if w.endswith("ed"): stems += [w[:-2], w[:-1], w[:-3] if len(w) > 4 and w[-3] == w[-4] else ""]
    if w.endswith("ing"): stems += [w[:-3], w[:-3] + "e", w[:-4] if len(w) > 5 and w[-4] == w[-5] else ""]
    if w.endswith("ly"): stems.append(w[:-2])
    if w.endswith("er"): stems += [w[:-2], w[:-1]]
    if w.endswith("est"): stems += [w[:-3], w[:-2]]
    return any(st and st in ENGLISH for st in stems)

# ------------------------------------------------------------------ patterns
_SEP = r"[ \t\-\.]?"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_OBFUSCATED_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+\s*(?:\(at\)|\[at\]|\sat\s)\s*[A-Za-z0-9\-]+(?:\s*(?:\(dot\)|\[dot\]|\sdot\s|\.)\s*[A-Za-z]{2,})+\b",
    re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[ .\-]?)?(?:\(\d{3}\)\s?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?!\d)|(?<![\d$])\d{3}[ .\-]\d{4}(?!\d)")
SSN_RE = re.compile(r"(?<!\d)\d{3}[ \-]\d{2}[ \-]\d{4}(?!\d)")
SSN_CUE_RE = re.compile(r"\b(?:ssn|ss#|social(?:\s+security)?(?:\s+(?:no|number|#))?)\b[^\d\n]{0,15}(\d{9}|\d{3}[ \-]?\d{2}[ \-]?\d{4})", re.I)
# Digit runs with optional single separators (space, dash, dot, newline) between groups.
DIGIT_RUN_RE = re.compile(r"(?<![\d$.,])\d(?:[ \-\.\n]?\d){5,22}(?![\d,]|\.\d)")
ACCOUNT_CUE_RE = re.compile(
    r"\b(?:acct|account|acc|card|loan|mortgage|checking|savings|cuenta|tarjeta)\b\.?\s*(?:#|no\.?|number|num|nbr|ending(?:\s+in)?|terminada\s+en|last\s+(?:4|four)(?:\s+digits)?(?:\s+(?:is|are))?)?\s*[:#]?\s*(?:x+|\*+)?(\d{4,})"
    r"|\b(?:ending|ends)\s+(?:in|with)\s+(\d{4})\b|\blast\s+(?:4|four)\s*(?:digits)?\s*(?:is|are|of|:)?\s*(?:my\s+\w+\s+)?(?:is\s+)?(\d{4})\b|\b[xX*]{2,}(\d{4})\b",
    re.I)
ROUTING_CUE_RE = re.compile(r"\b(?:routing|aba|rtn)\b[^\d\n]{0,15}(\d{9})", re.I)
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = rf"(?:\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}|{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}\s+{_MONTH}\.?\s+\d{{4}})"
DOB_RE = re.compile(rf"\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|born(?:\s+on)?|birthday(?:\s+is)?|fecha\s+de\s+nacimiento)\b[:\s\-]*({_DATE})", re.I)
_STREET = (r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way|Place|Pl|Terrace|Ter|"
           r"Circle|Cir|Highway|Hwy|Parkway|Pkwy|Trail|Trl|Square|Sq|Loop|Pike|Turnpike|Calle)")
ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9][A-Za-z0-9'’\-]*\.?\s+){{0,4}}{_STREET}\b\.?"
    r"(?:,?\s*(?:Apt|Apartment|Unit|Suite|Ste|#)\.?\s*[\w\-]+)?"
    r"(?:,\s*[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})?"
    r"(?:,?\s*[A-Z]{2})?(?:\s+\d{5}(?:-\d{4})?)?",
    re.I)
PO_BOX_RE = re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+\b", re.I)
_NUMWORD = r"(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"
SPELLED_DIGITS_RE = re.compile(rf"\b{_NUMWORD}(?:[\s,\-]+{_NUMWORD}){{6,}}\b", re.I)

# -------------------------------------------------------------------- names
_UP = "[A-ZÁÉÍÓÚÑÜÀÈÌÒÙÂÊÎÔÛÄËÏÖÇ]"
_LET = r"[^\W\d_]"
_NAME_WORD = rf"{_UP}(?:{_LET}|['’\-])+"
_NAME_WORD_ANYCASE = rf"{_LET}(?:{_LET}|['’\-])+"
HONORIFIC_RE = re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Sr|Sra|Srta)\.?\s+({_NAME_WORD}(?:\s+{_NAME_WORD})?)")
_CUES = (r"my\s+(?:son|daughter|husband|wife|mother|mom|father|dad|brother|sister|caregiver|aide|neighbou?r|friend|"
         r"grandson|granddaughter|grandmother|grandfather|grandma|grandpa|nephew|niece|partner|fianc[eé]e?|cousin|uncle|"
         r"aunt|roommate|landlord|boss|ex|ex-husband|ex-wife|stepson|stepdaughter|attorney|lawyer|accountant|"
         r"hijo|hija|esposo|esposa|madre|padre|hermano|hermana)"
         r"|(?:son|daughter|husband|wife|caregiver|aide|poa)(?:\s+is)?(?:\s+named)?"
         r"|named|called|teller|banker|rep|representative|agent|manager|supervisor|officer|associate|advisor|"
         r"specialist|employee|lady|guy|woman|man|gentleman|spoke\s+(?:to|with)|talked\s+(?:to|with)|asked\s+for|"
         r"helped\s+by|transferred\s+to|per|cx|cust|caller|customer|member|account\s+holder|signed|attn|c/o")
CUE_NAME_RE = re.compile(rf"\b(?:{_CUES})\b[,:]?\s+(?:(?:the|a|an)\s+)?({_NAME_WORD_ANYCASE}(?:\s+{_NAME_WORD_ANYCASE})?)", re.I)
SIGNOFF_RE = re.compile(
    r"(?:^|\n)\s*(?:thanks|thank\s+you|regards|best|best\s+regards|sincerely|respectfully|cheers|gracias|atentamente|saludos|-{1,2}|—)\s*[,!.]?\s*\n+\s*"
    rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})\s*(?:\n|$)", re.I)
CAP_SEQ_RE = re.compile(rf"\b({_NAME_WORD})((?:[ \t]+{_NAME_WORD})*)")   # names don't span lines


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _aba_ok(d: str) -> bool:
    if len(d) != 9:
        return False
    w = [3, 7, 1] * 3
    return sum(int(c) * k for c, k in zip(d, w)) % 10 == 0


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _base(word: str) -> str:
    return re.sub(r"['’]s$", "", word.strip(",.;:!?'’-"))


def _is_all_caps_text(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _looks_like_name_word(word: str, all_caps: bool) -> bool:
    w = word.strip("'’-")
    if len(w) < 2:
        return False
    low = w.lower()
    if low in KEEP_CAPITALIZED:
        return False
    if all_caps:
        return w.capitalize() in COMMON_FIRST_NAMES or w.capitalize() in AMBIGUOUS_FIRST_NAMES
    return w[0].isupper() and (w in COMMON_FIRST_NAMES or w in AMBIGUOUS_FIRST_NAMES or not is_english(w))


class Redactor:
    """Stateless detector set; one call per complaint."""

    def __init__(self, directory_names: list[str] | None = None):
        # Full names of every customer the bank knows. Matched case-insensitively.
        self.directory_names = sorted({n.strip() for n in (directory_names or []) if n.strip()}, key=len, reverse=True)

    # -------------------------------------------------------------- detection
    def detect(self, text: str, known: dict | None = None) -> list[Span]:
        known = known or {}
        spans: list[Span] = []
        add = lambda s, e, kind, src: spans.append(Span(s, e, kind, src, _PRIORITY.get(src, _PRIORITY.get(kind, 50))))
        all_caps = _is_all_caps_text(text)

        # 1. Known entities -------------------------------------------------
        for kind, values in self._known_values(known).items():
            for v in values:
                if kind in ("PHONE", "ACCOUNT"):
                    tail = _digits(v)[-7:] if kind == "PHONE" else _digits(v)
                    for m in DIGIT_RUN_RE.finditer(text):
                        if _digits(m.group(0)).endswith(tail) and len(tail) >= 4:
                            add(m.start(), m.end(), kind, "KNOWN")
                    for m in re.finditer(rf"(?<!\d){re.escape(_digits(v)[-4:])}(?!\d)", text) if kind == "ACCOUNT" else []:
                        add(m.start(), m.end(), kind, "KNOWN")
                    continue
                flags = re.I if (kind != "NAME" or all_caps or len(v) > 3) else 0
                pattern = rf"(?<!{_LET}){re.escape(v)}(?!{_LET})"
                for m in re.finditer(pattern, text, flags):
                    # Single-word name parts that are also English words ("Hope",
                    # "Price") only match when capitalized, unless text is all caps.
                    if kind == "NAME" and " " not in v and is_english(v) and not all_caps and not m.group(0)[0].isupper():
                        continue
                    add(m.start(), m.end(), "CUSTOMER_NAME" if kind == "NAME" else kind, "KNOWN")
        for full in self.directory_names:
            for m in re.finditer(rf"(?<!{_LET}){re.escape(full)}(?!{_LET})", text, re.I):
                add(m.start(), m.end(), "NAME", "KNOWN")

        # 2. Patterns ---------------------------------------------------------
        for m in EMAIL_RE.finditer(text):
            add(m.start(), m.end(), "EMAIL", "EMAIL")
        for m in EMAIL_OBFUSCATED_RE.finditer(text):
            if re.search(r"\b(?:dot|com|net|org|edu|gov)\b", m.group(0), re.I):
                add(m.start(), m.end(), "EMAIL", "EMAIL")
        for m in SSN_RE.finditer(text):
            add(m.start(), m.end(), "SSN", "SSN")
        for m in SSN_CUE_RE.finditer(text):
            add(m.start(1), m.end(1), "SSN", "SSN")
        for m in ROUTING_CUE_RE.finditer(text):
            add(m.start(1), m.end(1), "ROUTING", "ROUTING")
        for m in DOB_RE.finditer(text):
            add(m.start(1), m.end(1), "DOB", "DOB")
        for m in PHONE_RE.finditer(text):
            add(m.start(), m.end(), "PHONE", "PHONE")
        for m in SPELLED_DIGITS_RE.finditer(text):
            add(m.start(), m.end(), "PHONE", "PHONE")
        for m in DIGIT_RUN_RE.finditer(text):
            d = _digits(m.group(0))
            if 13 <= len(d) <= 19 and _luhn_ok(d):
                add(m.start(), m.end(), "CARD", "CARD")
            elif len(d) == 9 and _aba_ok(d):
                add(m.start(), m.end(), "ROUTING", "ROUTING")
            elif len(d) >= 6 and not _looks_like_date_or_time(m.group(0)) and not _looks_like_amount(m.group(0)):
                add(m.start(), m.end(), "ACCOUNT", "ACCOUNT")
        for m in ACCOUNT_CUE_RE.finditer(text):
            g = next(i for i in (1, 2, 3, 4) if m.group(i))
            add(m.start(g), m.end(g), "ACCOUNT", "ACCOUNT")
        for m in ADDRESS_RE.finditer(text):
            if re.search(r"[A-Za-z]", m.group(0)):
                add(m.start(), m.end(), "ADDRESS", "ADDRESS")
        for m in PO_BOX_RE.finditer(text):
            add(m.start(), m.end(), "ADDRESS", "ADDRESS")

        # 3. Name heuristics ------------------------------------------------
        for m in HONORIFIC_RE.finditer(text):
            add(m.start(1), m.end(1), "NAME", "NAME")
        for m in CUE_NAME_RE.finditer(text):
            s, e = self._trim_name(text, m.start(1), m.end(1), all_caps)
            if s is not None:
                add(s, e, "NAME", "NAME")
        for m in SIGNOFF_RE.finditer(text):
            words = m.group(1).split()
            if all(w.lower() not in KEEP_CAPITALIZED for w in words):
                add(m.start(1), m.end(1), "NAME", "NAME")
        for m in CAP_SEQ_RE.finditer(text):
            self._cap_sequence(text, m, all_caps, add)
        if all_caps:
            for m in re.finditer(rf"\b{_UP}(?:{_UP}|['’\-])+\b", text):
                if m.group(0).capitalize() in COMMON_FIRST_NAMES:
                    end = m.end()
                    nxt = re.match(rf"\s+({_UP}(?:{_UP}|['’\-])+)\b", text[end:])
                    if nxt and not is_english(nxt.group(1)) and nxt.group(1).lower() not in KEEP_CAPITALIZED:
                        end += nxt.end()
                    add(m.start(), end, "NAME", "NAME")
        return spans

    @staticmethod
    def _known_values(known: dict) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {"NAME": [], "EMAIL": [], "PHONE": [], "ACCOUNT": [], "ADDRESS": [], "DOB": []}
        for name in known.get("names", []):
            name = name.strip()
            if not name:
                continue
            out["NAME"].append(name)
            parts = [p for p in re.split(r"\s+", name) if len(p.strip(".")) > 1]
            out["NAME"].extend(parts)
        out["EMAIL"] = [e for e in known.get("emails", []) if e]
        out["PHONE"] = [p for p in known.get("phones", []) if p]
        out["ACCOUNT"] = [a for a in known.get("accounts", []) if a and len(_digits(a)) >= 4]
        out["ADDRESS"] = [a.split(",")[0] for a in known.get("addresses", []) if a]
        out["DOB"] = [d for d in known.get("dobs", []) if d]
        return out

    @staticmethod
    def _trim_name(text: str, s: int, e: int, all_caps: bool) -> tuple[int | None, int]:
        words = list(re.finditer(r"\S+", text[s:e]))
        kept = []
        for w in words:
            token = _base(w.group(0))
            if token and token[0].isupper() and token.lower() not in KEEP_CAPITALIZED and (
                    not is_english(token) or token in COMMON_FIRST_NAMES or token in AMBIGUOUS_FIRST_NAMES
                    or (all_caps and token.capitalize() in COMMON_FIRST_NAMES | AMBIGUOUS_FIRST_NAMES)):
                kept.append(w)
            else:
                break
        if not kept:
            return None, e
        return s + kept[0].start(), s + kept[-1].start() + len(kept[-1].group(0).rstrip(",.;:!?"))

    def _cap_sequence(self, text, m, all_caps, add):
        if all_caps:
            return
        first_raw = m.group(1)
        first = _base(first_raw)
        start = m.start(1)
        sentence_initial = start == 0 or re.search(r"(?:[.!?:]\s+|\n\s*|^\s*)$", text[max(0, start - 3):start]) is not None
        rest = m.group(2).split()
        words = [first_raw] + rest
        # A first name (unambiguous) anywhere, with any following capitalized surname.
        if first in COMMON_FIRST_NAMES and first.lower() not in KEEP_CAPITALIZED:
            end = start + len(first_raw)
            if rest and _base(rest[0]).lower() not in KEEP_CAPITALIZED:
                end = text.index(rest[0], end) + len(rest[0])
            add(start, end, "NAME", "NAME")
            return
        # Ambiguous first names ("Will", "Rose") need a capitalized surname after them.
        if first in AMBIGUOUS_FIRST_NAMES and rest and _base(rest[0]).lower() not in KEEP_CAPITALIZED and not sentence_initial:
            end = text.index(rest[0], start + len(first_raw)) + len(rest[0])
            add(start, end, "NAME", "NAME")
            return
        # Unknown capitalized words mid-sentence: probably a name (or a merchant).
        pos = start
        for w in words:
            wpos = text.index(w, pos)
            pos = wpos + len(w)
            initial = sentence_initial and wpos == start
            if initial or w.isupper():
                continue
            b = _base(w).lower()
            if not is_english(b) and b not in KEEP_CAPITALIZED and len(b) > 2:
                add(wpos, wpos + len(w), "NAME", "NAME")

    # ------------------------------------------------------------- redaction
    def redact(self, text: str, known: dict | None = None) -> Redaction:
        spans = self.detect(text, known)
        chosen = _resolve(spans)
        vault: dict[str, str] = {}
        by_value: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        found = []
        out = []
        cursor = 0
        for sp in chosen:
            original = text[sp.start:sp.end]
            kind = sp.kind
            norm = _digits(original) if kind in ("ACCOUNT", "CARD", "PHONE", "SSN", "ROUTING") else original.lower()
            key = (kind, norm)
            if key not in by_value:
                counters[kind] = counters.get(kind, 0) + 1
                token = f"⟦{kind}_{counters[kind]}⟧"
                by_value[key] = token
                vault[token] = original
            token = by_value[key]
            found.append({"token": token, "kind": kind, "source": sp.source})
            out.append(text[cursor:sp.start])
            out.append(token)
            cursor = sp.end
        out.append(text[cursor:])
        return Redaction(text="".join(out), vault=vault, found=found)


def _looks_like_amount(s: str) -> bool:
    return re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2}", s.strip()) is not None


def _looks_like_date_or_time(s: str) -> bool:
    d = _digits(s)
    # e.g. 20260903 or 09032026 typed without separators
    return len(d) == 8 and (d.startswith(("19", "20")) or d[4:].startswith(("19", "20")))


def _resolve(spans: list[Span]) -> list[Span]:
    """Merge overlapping spans into their union so nothing is half-redacted.

    The merged span takes the kind of its highest-priority member (ties go to
    the longer member), so a phone number inside an address stays one token.
    """
    merged: list[list[Span]] = []
    for sp in sorted(spans, key=lambda s: (s.start, -s.end)):
        if merged and sp.start < max(m.end for m in merged[-1]):
            merged[-1].append(sp)
        else:
            merged.append([sp])
    out = []
    for group in merged:
        lead = max(group, key=lambda s: (s.priority, s.end - s.start))
        out.append(Span(min(s.start for s in group), max(s.end for s in group), lead.kind, lead.source, lead.priority))
    return out
