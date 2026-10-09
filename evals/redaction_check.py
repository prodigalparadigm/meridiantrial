"""Redaction-only leak check: no model, no cost. Runs intake + redaction over a dataset and
reports which planted PII values would still be readable in the text a model would see.

    python3 -m evals.redaction_check data/synthetic data/holdout

data/holdout is the same generator with a different seed (new people and numbers). It was never
used to tune the redactor, so its number is the honest estimate.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from evals.run_eval import leaked
from meridian.gate import EgressGate
from meridian.intake import CustomerDirectory, normalize
from meridian.redact import Redactor


def check(dataset: Path) -> dict:
    crm = json.loads((dataset / "crm.json").read_text())
    d = CustomerDirectory(crm)
    r, g = Redactor(d.names()), EgressGate(d.customers)
    labels = {l["id"]: l for l in map(json.loads, (dataset / "labels.jsonl").read_text().splitlines())}
    in_text, metadata_only, leaks, blocked = Counter(), Counter(), Counter(), 0
    over = Counter()
    details = []
    for raw in map(json.loads, (dataset / "complaints.jsonl").read_text().splitlines()):
        c = normalize(raw, d)
        red = r.redact(c.text, c.known)
        blocked += not g.check(red.text).allowed
        pii = labels[raw["id"]]["pii"]
        for p in pii:
            if p["value"].lower() not in c.text.lower():
                metadata_only[p["type"]] += 1      # sender fields: never sent to a model at all
                continue
            in_text[p["type"]] += 1
            if leaked(p["value"], p["type"], red.text):
                leaks[p["type"]] += 1
                details.append({"id": raw["id"], "type": p["type"]})
        vals = [p["value"].lower() for p in pii]
        for tok, orig in red.vault.items():
            if "NAME" in tok and not any(orig.lower() in v or v in orig.lower() for v in vals):
                over[orig] += 1
    return {"dataset": dataset.name, "complaints": len(labels), "in_text": dict(in_text),
            "in_text_total": sum(in_text.values()), "metadata_only_total": sum(metadata_only.values()),
            "leaked": dict(leaks), "leaked_total": sum(leaks.values()), "leak_details": details,
            "gate_blocks_after_redaction": blocked, "non_pii_words_hidden_as_names": sum(over.values()),
            "examples_hidden": [w for w, _ in over.most_common(8)]}


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["data/synthetic", "data/holdout"]:
        res = check(Path(arg))
        print(f"{res['dataset']}: {res['leaked_total']}/{res['in_text_total']} in-text PII values readable after redaction "
              f"{res['leaked'] or ''}; {res['metadata_only_total']} more in sender fields never sent; "
              f"{res['non_pii_words_hidden_as_names']} non-PII words hidden as names; gate blocks {res['gate_blocks_after_redaction']}")
