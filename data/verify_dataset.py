#!/usr/bin/env python3
"""
Verify the synthetic complaint dataset.  Exits non-zero on any failure.

  python3 verify_dataset.py [--dir data/synthetic] [--skip-determinism]

Checks: id parity and count; schema of complaints, labels and CRM; every label value valid against
config/*.json; every PII value is an exact substring of the payload; PII coverage per record and per type;
composition minimums; CRM consistency; fictional-data rules (555-01xx phones, example.* emails,
9xx SSNs, Luhn-valid test-prefix cards, non-ABA routing numbers); heuristic sweeps for PII-looking
strings that were NOT labeled; byte-for-byte determinism of the generator.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "config"
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import generate as G  # noqa: E402  (name pools for the unlabeled-name sweep)

TAGS = ["buried_regulatory", "hard_negative", "multi_issue", "non_complaint", "call_shorthand", "non_english",
        "low_literacy", "third_party_writer", "duplicate", "pii_edge", "injection"]
PII_TYPES = ["CUSTOMER_NAME", "THIRD_PARTY_NAME", "EMAIL", "PHONE", "ACCOUNT_NUMBER", "CARD_NUMBER", "SSN", "DOB",
             "ADDRESS", "ROUTING_NUMBER"]
TOPICS = ["Fees", "Card", "Online Banking", "Loan", "Mortgage", "Branch Service", "Fraud", "Other"]
PAYLOAD_KEYS = {"email": ["from", "subject", "body"],
                "web_form": ["name", "email", "phone", "account_last4", "topic_selected", "message"],
                "call_note": ["agent_id", "caller_name", "callback", "note"]}
WORD_DIGITS = {"oh": "0", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
               "seven": "7", "eight": "8", "nine": "9"}

failures: list[str] = []


def fail(msg: str):
    failures.append(msg)


def check(cond, msg):
    if not cond:
        fail(msg)
    return bool(cond)


def table(title, counter: Counter, order=None, total=None):
    print(f"\n{title}")
    keys = order if order is not None else sorted(counter, key=lambda k: (-counter[k], str(k)))
    w = max([len(str(k)) for k in keys] + [8])
    for k in keys:
        pct = f"  {100 * counter[k] / total:5.1f}%" if total else ""
        print(f"  {str(k):<{w}}  {counter[k]:>4}{pct}")


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def spoken_to_digits(s: str) -> str:
    toks = re.findall(r"[a-z]+|\d", s.lower())
    return "".join(WORD_DIGITS.get(t, t if t.isdigit() else "") for t in toks)


def luhn_ok(num: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(num)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def aba_ok(r: str) -> bool:
    d = [int(c) for c in r]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def payload_fields(payload: dict) -> list[tuple[str, str]]:
    return [(k, v) for k, v in payload.items() if isinstance(v, str)]


def label_spans(fields, pii):
    """All (field_key, start, end) spans covered by any labeled PII value."""
    spans = []
    for k, text in fields:
        for e in pii:
            v = e["value"]
            if not v:
                continue
            i = text.find(v)
            while i != -1:
                spans.append((k, i, i + len(v), e["type"]))
                i = text.find(v, i + 1)
    return spans


def covered(spans, key, a, b, types=None):
    return any(k == key and s <= a and b <= e and (types is None or t in types) for k, s, e, t in spans)


def load_jsonl(path: Path):
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as ex:
            fail(f"{path.name}:{n} invalid JSON: {ex}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE / "synthetic"))
    ap.add_argument("--skip-determinism", action="store_true")
    args = ap.parse_args()
    ddir = Path(args.dir)

    depts_cfg = json.loads((CONFIG / "departments.json").read_text())
    tax_cfg = json.loads((CONFIG / "compliance_taxonomy.json").read_text())
    sev_cfg = json.loads((CONFIG / "severity_rubric.json").read_text())
    DEPTS = [d["id"] for d in depts_cfg["departments"]]
    CATS = [c["id"] for c in tax_cfg["categories"]]
    DIMS = {d["id"]: d for d in sev_cfg["dimensions"]}
    FIN = DIMS["financial_impact"]["values"]

    complaints = load_jsonl(ddir / "complaints.jsonl")
    labels = load_jsonl(ddir / "labels.jsonl")
    crm = json.loads((ddir / "crm.json").read_text(encoding="utf-8"))
    customers = crm.get("customers", [])
    by_cid = {c["customer_id"]: c for c in customers}

    # ---------------------------------------------------------------- ids
    cids = [c["id"] for c in complaints]
    lids = [l["id"] for l in labels]
    check(len(cids) == len(set(cids)), "duplicate ids in complaints.jsonl")
    check(len(lids) == len(set(lids)), "duplicate ids in labels.jsonl")
    check(set(cids) == set(lids), f"id sets differ: only in complaints {sorted(set(cids) - set(lids))[:5]}, "
                                  f"only in labels {sorted(set(lids) - set(cids))[:5]}")
    check(len(complaints) >= 150, f"only {len(complaints)} complaints (need >= 150)")
    print(f"records: {len(complaints)} complaints, {len(labels)} labels, {len(customers)} CRM customers")
    C = {c["id"]: c for c in complaints}
    L = {l["id"]: l for l in labels}

    # ---------------------------------------------------------------- complaint schema
    for c in complaints:
        cid = c.get("id")
        check(list(c.keys()) == ["id", "channel", "received_at", "payload"], f"{cid}: complaint keys {list(c.keys())}")
        check(re.fullmatch(r"C-\d{4}", cid or "") is not None, f"{cid}: bad id format")
        ch = c.get("channel")
        if not check(ch in PAYLOAD_KEYS, f"{cid}: bad channel {ch}"):
            continue
        p = c["payload"]
        check(list(p.keys()) == PAYLOAD_KEYS[ch], f"{cid}: payload keys {list(p.keys())} != {PAYLOAD_KEYS[ch]}")
        check(all(isinstance(v, str) for v in p.values()), f"{cid}: non-string payload value")
        try:
            t = dt.datetime.fromisoformat(c["received_at"])
            check(t.utcoffset() == dt.timedelta(hours=-4), f"{cid}: received_at offset must be -04:00")
            check(dt.date(2026, 9, 1) <= t.date() <= dt.date(2026, 9, 30), f"{cid}: received_at outside Sept 2026")
        except Exception as ex:  # noqa: BLE001
            fail(f"{cid}: bad received_at {c.get('received_at')!r}: {ex}")
        if ch == "email":
            check(re.fullmatch(r".+ <[^<>@\s]+@[^<>@\s]+>", p["from"]) is not None, f"{cid}: from header not 'Name <addr>'")
            check(p["body"].strip() != "", f"{cid}: empty body")
        elif ch == "web_form":
            check(p["topic_selected"] in TOPICS, f"{cid}: bad topic {p['topic_selected']}")
            check(p["account_last4"] == "" or re.fullmatch(r"\d{4}", p["account_last4"]) is not None, f"{cid}: bad account_last4")
            check(p["message"].strip() != "", f"{cid}: empty message")
        else:
            check(re.fullmatch(r"CSR-\d{4}", p["agent_id"]) is not None, f"{cid}: bad agent_id")
            check(p["note"].strip() != "", f"{cid}: empty note")
        blob = json.dumps(p).lower()
        for leak in ("reg_categories", "primary_department", "acceptable_departments", "\"scenario\"", "customer_id"):
            if leak in blob and "injection" not in L.get(cid, {}).get("tags", []):
                fail(f"{cid}: label vocabulary '{leak}' leaked into payload")

    # ---------------------------------------------------------------- label validity
    for l in labels:
        lid = l.get("id")
        exp = ["id", "scenario", "customer_id", "regulatory", "reg_categories", "primary_department",
               "acceptable_departments", "facts", "pii", "tags", "notes"]
        check(list(l.keys()) == exp, f"{lid}: label keys {list(l.keys())}")
        check(isinstance(l.get("scenario"), str) and re.fullmatch(r"[a-z0-9_]+", l["scenario"] or "") is not None, f"{lid}: bad scenario")
        cust = l.get("customer_id")
        check(cust is None or re.fullmatch(r"CUST-\d{5}", cust) is not None, f"{lid}: bad customer_id {cust}")
        check(isinstance(l.get("regulatory"), bool), f"{lid}: regulatory not bool")
        cats = l.get("reg_categories", [])
        check(all(x in CATS for x in cats), f"{lid}: unknown reg category in {cats}")
        check(len(cats) == len(set(cats)), f"{lid}: duplicate reg categories")
        check(bool(cats) == l.get("regulatory"), f"{lid}: regulatory={l.get('regulatory')} but categories={cats}")
        check(l.get("primary_department") in DEPTS, f"{lid}: bad primary_department {l.get('primary_department')}")
        acc = l.get("acceptable_departments", [])
        check(all(a in DEPTS for a in acc), f"{lid}: unknown acceptable department in {acc}")
        check(acc[:1] == [l.get("primary_department")], f"{lid}: acceptable_departments must start with primary")
        check(len(acc) == len(set(acc)), f"{lid}: duplicate acceptable departments")
        f = l.get("facts", {})
        check(set(f.keys()) == set(DIMS.keys()), f"{lid}: facts keys {sorted(f.keys())}")
        check(f.get("financial_impact") in FIN, f"{lid}: bad financial_impact {f.get('financial_impact')}")
        for k, d in DIMS.items():
            if d["type"] == "bool":
                check(isinstance(f.get(k), bool), f"{lid}: fact {k} not bool")
        check(all(t in TAGS for t in l.get("tags", [])), f"{lid}: unknown tag in {l.get('tags')}")
        check(isinstance(l.get("pii"), list), f"{lid}: pii not a list")
        for e in l.get("pii", []):
            check(e.get("type") in PII_TYPES and isinstance(e.get("value"), str) and e["value"], f"{lid}: bad pii entry {e}")
        check(isinstance(l.get("notes"), str) and l["notes"].strip(), f"{lid}: empty notes")
        if l.get("tags") and "call_shorthand" in l["tags"]:
            check(C[lid]["channel"] == "call_note", f"{lid}: call_shorthand on a non-call record")

    # ---------------------------------------------------------------- PII exact-substring + coverage
    pii_types = Counter()
    bad_substr = []
    for l in labels:
        c = C[l["id"]]
        texts = [v for _, v in payload_fields(c["payload"])]
        check(len(l["pii"]) >= 1, f"{l['id']}: no PII entries")
        seen = set()
        for e in l["pii"]:
            pii_types[e["type"]] += 1
            if not any(e["value"] in t for t in texts):
                bad_substr.append((l["id"], e["type"], e["value"]))
            key = (e["type"], e["value"])
            check(key not in seen, f"{l['id']}: duplicate PII entry {key}")
            seen.add(key)
    for rid, typ, val in bad_substr:
        fail(f"{rid}: PII value not an exact substring of payload: {typ} {val!r}")
    table("PII entries by type", pii_types, PII_TYPES)
    for t in PII_TYPES:
        check(pii_types[t] >= 3, f"PII type {t} appears only {pii_types[t]} times (need >= 3)")

    # ---------------------------------------------------------------- unlabeled-PII sweeps (completeness heuristics)
    name_tokens = G.all_name_tokens()
    ambiguous = set(G.AMBIGUOUS_NAME_TOKENS)
    strict = [t for t in name_tokens if t not in ambiguous]
    rx_strict = re.compile(r"(?<![\w'])(" + "|".join(sorted(map(re.escape, strict), key=len, reverse=True)) + r")(?![\w'])", re.I)
    rx_amb = re.compile(r"(?<![\w'])(" + "|".join(sorted(map(re.escape, ambiguous), key=len, reverse=True)) + r")(?![\w'])")
    rx_email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    rx_spoken_email = re.compile(r"[\w ]+? at example dot (?:com|net|org)", re.I)
    rx_phone = re.compile(r"(?<![\d\w])(?:\+1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]\d{4}(?![\d])")
    rx_ssn = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
    rx_digits = re.compile(r"(?<![\d])\d(?:[ \n-]?\d){8,}(?![\d])")
    unlabeled = Counter()
    all_emails, all_phone_like = [], []
    for l in labels:
        c = C[l["id"]]
        fields = payload_fields(c["payload"])
        spans = label_spans(fields, l["pii"])
        for k, text in fields:
            if k in ("agent_id", "topic_selected", "account_last4"):
                continue
            caps = sum(ch.isupper() for ch in text) > 0.6 * max(1, sum(ch.isalpha() for ch in text))
            for m in rx_strict.finditer(text):
                if not covered(spans, k, m.start(), m.end()):
                    fail(f"{l['id']}: unlabeled name-like token {m.group(0)!r} in {k}")
                    unlabeled["name"] += 1
            if not caps:
                for m in rx_amb.finditer(text):
                    pre = text[:m.start()].rstrip()
                    if pre == "" or pre[-1] in ".!?:\n\"(":
                        continue
                    if not covered(spans, k, m.start(), m.end()):
                        fail(f"{l['id']}: unlabeled name-like token {m.group(0)!r} in {k}")
                        unlabeled["name"] += 1
            for m in rx_email.finditer(text):
                all_emails.append((l["id"], m.group(0)))
                if not covered(spans, k, m.start(), m.end(), {"EMAIL"}):
                    fail(f"{l['id']}: unlabeled email {m.group(0)!r}")
                    unlabeled["email"] += 1
            for m in rx_spoken_email.finditer(text):
                if not any(e["type"] == "EMAIL" and e["value"].endswith(m.group(0).split(" at ")[-1]) and e["value"] in text
                           for e in l["pii"]):
                    fail(f"{l['id']}: unlabeled spoken email near {m.group(0)!r}")
            for m in rx_phone.finditer(text):
                if covered(spans, k, m.start(), m.end()):
                    if covered(spans, k, m.start(), m.end(), {"PHONE"}):
                        all_phone_like.append((l["id"], m.group(0)))
                    continue
                fail(f"{l['id']}: unlabeled phone-like string {m.group(0)!r} in {k}")
                unlabeled["phone"] += 1
            for m in rx_ssn.finditer(text):
                if not covered(spans, k, m.start(), m.end()):
                    fail(f"{l['id']}: unlabeled SSN-like string {m.group(0)!r}")
                    unlabeled["ssn"] += 1
            for m in rx_digits.finditer(text):
                if not covered(spans, k, m.start(), m.end()):
                    fail(f"{l['id']}: unlabeled long digit run {m.group(0)!r} in {k}")
                    unlabeled["digits"] += 1
    print(f"\nunlabeled-PII sweep: {sum(unlabeled.values())} findings {dict(unlabeled)}")

    # ---------------------------------------------------------------- fictional-data rules
    n_phone = n_email = n_ssn = n_card = n_route = 0
    for l in labels:
        for e in l["pii"]:
            v, t = e["value"], e["type"]
            if t == "PHONE":
                n_phone += 1
                d = digits(v) or spoken_to_digits(v)
                if not d:
                    d = spoken_to_digits(v)
                check(re.search(r"55501\d\d$", d) is not None and len(d) in (7, 10, 11),
                      f"{l['id']}: phone {v!r} is not a 555-01xx number")
            elif t == "EMAIL":
                n_email += 1
                dom = v.split("@")[-1].lower() if "@" in v else re.sub(r"\s+dot\s+", ".", v.lower().split(" at ")[-1])
                check(dom in ("example.com", "example.net", "example.org"), f"{l['id']}: email {v!r} not on example.* domain")
            elif t == "SSN":
                n_ssn += 1
                check(len(digits(v)) == 9 and digits(v).startswith("9"), f"{l['id']}: SSN {v!r} not a 9xx number")
            elif t == "CARD_NUMBER":
                n_card += 1
                d = digits(v)
                check(len(d) == 16 and luhn_ok(d) and (d.startswith("400000") or d.startswith("555500")),
                      f"{l['id']}: card {v!r} fails Luhn/test-prefix rule")
            elif t == "ROUTING_NUMBER":
                n_route += 1
                d = digits(v)
                check(len(d) == 9 and not aba_ok(d), f"{l['id']}: routing {v!r} passes ABA checksum (could be real)")
    for rid, em in all_emails:
        check(em.split("@")[1].lower() in ("example.com", "example.net", "example.org"), f"{rid}: email {em} not example.*")
    for rid, ph in all_phone_like:
        check(re.search(r"55501\d\d$", digits(ph)) is not None, f"{rid}: phone-like {ph} not 555-01xx")
    for c in complaints:
        for k, text in payload_fields(c["payload"]):
            for m in rx_ssn.finditer(text):
                check(m.group(0).startswith("9"), f"{c['id']}: SSN-like {m.group(0)} does not start with 9")
    for cu in customers:
        check(cu["email"].split("@")[1] in ("example.com", "example.net", "example.org"), f"CRM {cu['customer_id']}: email domain")
        check(re.fullmatch(r"\d{3}-555-01\d\d", cu["phone"]) is not None, f"CRM {cu['customer_id']}: phone {cu['phone']}")
        check(re.fullmatch(r"\d{4}-\d{2}-\d{2}", cu["dob"]) is not None, f"CRM {cu['customer_id']}: dob format")
        check(re.fullmatch(r".+, .+, [A-Z]{2} \d{5}", cu["address"]) is not None, f"CRM {cu['customer_id']}: address format")
        for a in cu["accounts"]:
            check(a["type"] in ("checking", "savings", "credit_card", "debit_card", "auto_loan", "mortgage", "personal_loan"),
                  f"CRM {cu['customer_id']}: bad account type {a['type']}")
            if a["type"].endswith("card"):
                d = a["number"]
                check(luhn_ok(d) and (d.startswith("400000") or d.startswith("555500")), f"CRM {cu['customer_id']}: card {d}")
    print(f"fictional-data checks: {n_phone} phones, {n_email} emails, {n_ssn} SSNs, {n_card} cards, {n_route} routing numbers, "
          f"{len(customers)} CRM rows")

    # ---------------------------------------------------------------- CRM consistency
    crm_emails = {c["email"].lower(): c for c in customers}
    crm_phones = {digits(c["phone"]): c for c in customers}
    crm_numbers = {a["number"]: c["customer_id"] for c in customers for a in c["accounts"]}
    n_matched = n_null = n_tp = 0

    def name_overlap(a: str, b: str) -> bool:
        ta = {t.casefold() for t in re.findall(r"[^\W\d_]+", G.fold(a))}
        tb = {t.casefold() for t in re.findall(r"[^\W\d_]+", G.fold(b))}
        return bool(ta & tb)

    for l in labels:
        c = C[l["id"]]
        p = c["payload"]
        ch = c["channel"]
        meta_email = re.search(r"<([^>]+)>", p["from"]).group(1) if ch == "email" else p.get("email")
        meta_phone = p.get("phone") if ch == "web_form" else p.get("callback")
        meta_name = p["from"].split(" <")[0] if ch == "email" else (p.get("name") or p.get("caller_name"))
        cust = l["customer_id"]
        if cust is None:
            n_null += 1
            check(not (meta_email and meta_email.lower() in crm_emails), f"{l['id']}: customer_id null but sender email is in CRM")
            check(not (meta_phone and digits(meta_phone)[-10:] in crm_phones), f"{l['id']}: customer_id null but phone is in CRM")
            continue
        if not check(cust in by_cid, f"{l['id']}: customer_id {cust} not in crm.json"):
            continue
        cu = by_cid[cust]
        if "third_party_writer" in l["tags"]:
            n_tp += 1
            check(not (meta_email and meta_email.lower() == cu["email"].lower()), f"{l['id']}: third-party writer uses holder's email")
            holder_named = any(e["type"] == "CUSTOMER_NAME" and name_overlap(e["value"], cu["name"]) for e in l["pii"])
            check(holder_named, f"{l['id']}: third-party record does not name the account holder")
            continue
        n_matched += 1
        if meta_email:
            check(meta_email.lower() == cu["email"].lower(), f"{l['id']}: sender email {meta_email} != CRM {cu['email']}")
        if meta_phone:
            check(digits(meta_phone)[-10:] == digits(cu["phone"]), f"{l['id']}: phone {meta_phone} != CRM {cu['phone']}")
        check(name_overlap(meta_name, cu["name"]), f"{l['id']}: metadata name {meta_name!r} does not match CRM {cu['name']!r}")
        if ch == "web_form" and p["account_last4"]:
            check(any(a["number"].endswith(p["account_last4"]) for a in cu["accounts"]),
                  f"{l['id']}: account_last4 {p['account_last4']} not on customer's accounts")
    for l in labels:
        for e in l["pii"]:
            if e["type"] in ("ACCOUNT_NUMBER", "CARD_NUMBER"):
                check(digits(e["value"]) in crm_numbers, f"{l['id']}: {e['type']} {e['value']!r} not found in CRM accounts")
    complainant_ids = {l["customer_id"] for l in labels if l["customer_id"]}
    decoys = len(customers) - len(complainant_ids)
    names = Counter(c["name"] for c in customers)
    print(f"CRM: {n_matched} records matched on metadata, {n_tp} third-party-writer records, {n_null} non-customer records; "
          f"{len(complainant_ids)} distinct complainant customers, {decoys} CRM rows that never complain; "
          f"{sum(1 for v in names.values() if v > 1)} duplicated names in CRM")
    check(decoys >= 40, f"only {decoys} non-complaining CRM customers (need ~40)")

    # ---------------------------------------------------------------- composition
    n = len(labels)
    chan = Counter(C[l["id"]]["channel"] for l in labels)
    table("By channel", chan, ["email", "web_form", "call_note"], n)
    check(0.35 <= chan["email"] / n <= 0.45, "email share outside 35-45%")
    check(0.30 <= chan["web_form"] / n <= 0.40, "web_form share outside 30-40%")
    check(0.20 <= chan["call_note"] / n <= 0.30, "call_note share outside 20-30%")

    reg = sum(1 for l in labels if l["regulatory"])
    print(f"\nRegulatory-positive: {reg}/{n} = {100 * reg / n:.1f}%")
    check(0.33 <= reg / n <= 0.42, "regulatory share outside ~35-40% (allowed 33-42%)")
    cat = Counter(x for l in labels for x in l["reg_categories"])
    table("By regulatory category (records carrying it)", cat, CATS)
    for x in CATS:
        need = 4 if x in ("error_resolution", "fee_practices", "unfair_treatment") else 2
        check(cat[x] >= need, f"category {x} has {cat[x]} records (need >= {need})")

    dep = Counter(l["primary_department"] for l in labels)
    table("By primary department", dep, DEPTS, n)
    for d in DEPTS:
        check(dep[d] >= 6, f"department {d} is primary for {dep[d]} records (need >= 6)")
    multi_dept = sum(1 for l in labels if len(l["acceptable_departments"]) > 1)
    print(f"  records with >1 acceptable department: {multi_dept}")

    tags = Counter(t for l in labels for t in l["tags"])
    table("By tag", tags, TAGS)
    for t in TAGS:
        check(tags[t] >= 2, f"tag {t} appears {tags[t]} times (need multiple)")
    check(3 <= tags["non_english"] <= 5, f"non_english count {tags['non_english']} not in 3-5")
    check(tags["injection"] >= 10, f"only {tags['injection']} injection records")
    core = n - tags["injection"]
    print(f"  core records: {core}, adversarial (injection): {tags['injection']}")
    check(core >= 145, f"core set only {core}")
    scen = Counter(l["scenario"] for l in labels)
    print(f"  distinct scenarios (archetypes): {len(scen)}")
    check(len(scen) >= 50, f"only {len(scen)} archetypes")

    # duplicates: verify pairs
    dups = [l for l in labels if "duplicate" in l["tags"]]
    pairs = set()
    for l in dups:
        m = re.search(r"duplicate of (C-\d{4})", l["notes"])
        if not check(m is not None, f"{l['id']}: duplicate without partner id in notes"):
            continue
        o = L.get(m.group(1))
        if not check(o is not None and "duplicate" in o["tags"], f"{l['id']}: partner {m.group(1)} missing or untagged"):
            continue
        check(f"duplicate of {l['id']}" in o["notes"], f"{l['id']}: partner {o['id']} does not point back")
        check(o["customer_id"] == l["customer_id"] and o["customer_id"] is not None, f"{l['id']}: duplicate pair customer mismatch")
        check(o["scenario"] == l["scenario"] and o["reg_categories"] == l["reg_categories"], f"{l['id']}: duplicate pair labels differ")
        check(C[o["id"]]["channel"] != C[l["id"]]["channel"], f"{l['id']}: duplicate pair on same channel")
        pairs.add(tuple(sorted([l["id"], o["id"]])))
    print(f"  duplicate pairs: {len(pairs)} {sorted(pairs)}")
    check(3 <= len(pairs) <= 4, f"{len(pairs)} duplicate pairs (need 3-4)")

    # facts
    fin = Counter(l["facts"]["financial_impact"] for l in labels)
    table("financial_impact", fin, FIN, n)
    for v in FIN:
        check(fin[v] >= 3, f"financial_impact={v} only {fin[v]} times")
    print("\nboolean facts (true count)")
    for k in DIMS:
        if DIMS[k]["type"] == "bool":
            t = sum(1 for l in labels if l["facts"][k])
            print(f"  {k:<24} {t:>4}")
            check(5 <= t <= n - 5, f"fact {k} true {t} times; needs to vary")
    F = [l["facts"] for l in labels]
    contrasts = {
        "(a) vulnerable, little money": [l["id"] for l in labels if l["facts"]["vulnerability_indicated"]
                                         and l["facts"]["financial_impact"] in ("none", "under_100")],
        "(b) >$1000, calm (no threat), not vulnerable": [l["id"] for l in labels if l["facts"]["financial_impact"] == "over_1000"
                                                        and not l["facts"]["vulnerability_indicated"]
                                                        and not l["facts"]["escalation_threat"]
                                                        and not l["facts"]["time_sensitive"]],
        "(c) time-sensitive, small/no money": [l["id"] for l in labels if l["facts"]["time_sensitive"]
                                               and l["facts"]["financial_impact"] in ("none", "under_100")],
        "(d) threatening, trivial, non-regulatory": [l["id"] for l in labels if l["facts"]["escalation_threat"]
                                                     and l["facts"]["financial_impact"] == "none" and not l["regulatory"]],
    }
    print("\nseverity contrasts")
    for k, ids in contrasts.items():
        print(f"  {k:<46} {len(ids):>3}  e.g. {ids[:4]}")
        check(len(ids) >= 2, f"severity contrast {k} has {len(ids)} examples")
    _ = F

    # ---------------------------------------------------------------- determinism
    if not args.skip_determinism:
        # temp dirs live inside data/ and are removed afterwards
        with tempfile.TemporaryDirectory(dir=HERE, prefix=".det_") as t1, tempfile.TemporaryDirectory(dir=HERE, prefix=".det_") as t2:
            for t in (t1, t2):
                subprocess.run([sys.executable, "-B", str(HERE / "generate.py"), "--out", t], check=True, capture_output=True)
            for name in ("complaints.jsonl", "labels.jsonl", "crm.json"):
                a, b = (Path(t1) / name).read_bytes(), (Path(t2) / name).read_bytes()
                check(a == b, f"determinism: {name} differs between two runs")
                check(a == (ddir / name).read_bytes(), f"determinism: {name} in {ddir} differs from a fresh run (regenerate)")
        print("\ndeterminism: two fresh runs compared byte-for-byte with each other and with the files on disk")

    # ---------------------------------------------------------------- verdict
    print()
    if failures:
        print(f"FAIL: {len(failures)} problem(s)")
        for f_ in failures[:200]:
            print("  - " + f_)
        if len(failures) > 200:
            print(f"  ... and {len(failures) - 200} more")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
