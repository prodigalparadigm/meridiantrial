"""Run the synthetic month through the pipeline and score it against the sidecar labels.

    python3 -m evals.run_eval                 # auto: replay recorded responses, call Claude for anything new
    python3 -m evals.run_eval --mode replay   # offline; fails closed on any payload not recorded
    python3 -m evals.run_eval --limit 10      # quick look

Writes evals/results/<run>.json and regenerates docs/EVIDENCE.md.
The labels are read only here, after the pipeline has run; the pipeline never sees them.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from meridian import config, policy
from meridian.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "synthetic"
CACHE = ROOT / "evals" / "llm_cache"
MONTHLY_VOLUME = 1400
NUMERIC_PII = {"ACCOUNT_NUMBER", "CARD_NUMBER", "SSN", "PHONE", "ROUTING_NUMBER"}
TOKEN_RE = re.compile(r"⟦[^⟧]*⟧")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ------------------------------------------------------------------ leak scan
def leaked(value: str, kind: str, payload: str) -> bool:
    text = TOKEN_RE.sub(" ", payload)
    if kind in NUMERIC_PII and re.search(r"\d", value):
        digits = re.sub(r"\D", "", value)
        runs = [re.sub(r"\D", "", r) for r in re.findall(r"\d(?:[ \-.\n]?\d)*", text)]
        if len(digits) >= 4 and any(digits in r for r in runs):
            return True
        groups = [g for g in re.split(r"\D+", value) if len(g) >= 4]
        return any(g in runs for g in groups)
    if kind in ("CUSTOMER_NAME", "THIRD_PARTY_NAME"):
        return re.search(rf"(?<![^\W\d_]){re.escape(value)}(?![^\W\d_])", text) is not None
    if kind == "ADDRESS":
        street = value.split(",")[0].strip()          # the street line alone identifies a household
        return value.lower() in text.lower() or street.lower() in text.lower()
    return value.lower() in text.lower()


def scan_leaks(labels: dict, egress: list[dict], originals: dict[str, str]) -> dict:
    by_case = defaultdict(list)
    for e in egress:
        by_case[e["case_id"]].append(e["user"])
    planted, leaks = Counter(), Counter()
    metadata_only = 0
    details = []
    for cid, lab in labels.items():
        payloads = by_case.get(cid, [])
        for p in lab["pii"]:
            if p["value"].lower() not in originals.get(cid, "").lower():
                metadata_only += 1   # sender fields: never part of any payload, by design
                continue
            planted[p["type"]] += 1
            hit = next((pl for pl in payloads if leaked(p["value"], p["type"], pl)), None)
            if hit:
                leaks[p["type"]] += 1
                i = TOKEN_RE.sub(" ", hit).lower().find(p["value"].lower()[:6])
                details.append({"id": cid, "type": p["type"], "value_length": len(p["value"]),
                                "context": TOKEN_RE.sub(" ", hit)[max(0, i - 40): i + 60] if i >= 0 else ""})
    return {"payloads_scanned": len(egress), "cases_with_payloads": len(by_case), "metadata_only": metadata_only,
            "planted": dict(planted), "leaked": dict(leaks), "total_planted": sum(planted.values()),
            "total_leaked": sum(leaks.values()), "details": details}


# ------------------------------------------------------------------- scoring
PARALLEL = {"regulatory_screener", "triage_analyst", "router"}


def model_seconds(case: dict) -> dict:
    """Latency from each call's recorded live timing, so replayed runs report the real figures.

    The three analysis agents run in parallel, so the flag is known after the slowest of them;
    drafting and review run after that, one call at a time.
    """
    calls = case.get("calls", [])
    par = [c["latency_s"] for c in calls if c["agent"] in PARALLEL and c.get("latency_s")]
    seq = [c["latency_s"] for c in calls if c["agent"] not in PARALLEL and c.get("latency_s")]
    if case["regulatory"]["sources"]["tripwire"]:
        to_flag = 0.0                      # the local tripwire queues it for Compliance before any model call
    else:
        to_flag = max(par) if par else None
    return {"to_flag": to_flag, "end_to_end": (max(par) if par else 0) + sum(seq) or None}


def tripwire_terms(cases: dict, labels: dict) -> list[dict]:
    """Per-term record for Compliance: false flags, true flags, and true flags the model screener missed."""
    from meridian import config
    stats = defaultdict(lambda: {"false": 0, "true": 0, "true_model_missed": 0})
    for i, c in cases.items():
        text = c.get("_original", "")
        truth = labels[i]["regulatory"]
        model = bool((c["regulatory"]["sources"]["screener"] or {}).get("potential_regulatory"))
        for terms in config.tripwire_terms().values():
            for t in terms:
                if re.search(t, text, re.I):
                    s = stats[t]
                    s["false" if not truth else "true"] += 1
                    s["true_model_missed"] += truth and not model
    rows = [{"term": t, **v} for t, v in stats.items()]
    return sorted(rows, key=lambda r: (-r["false"], r["true"]))


def rate(n, d):
    return round(n / d, 4) if d else None


def score(cases: dict, labels: dict) -> dict:
    ids = sorted(labels)
    out: dict = {}

    # Regulatory
    tp = fp = fn = tn = 0
    trip_tp = scr_tp = 0
    missed, false_flags = [], []
    cat_hit, cat_total = Counter(), Counter()
    for i in ids:
        c, l = cases[i], labels[i]
        pred, truth = c["regulatory"]["flagged"], l["regulatory"]
        trip = bool(c["regulatory"]["sources"]["tripwire"])
        scr = bool((c["regulatory"]["sources"]["screener"] or {}).get("potential_regulatory"))
        if truth:
            trip_tp += trip
            scr_tp += scr
            for cat in l["reg_categories"]:
                cat_total[cat] += 1
                cat_hit[cat] += cat in c["regulatory"]["categories"]
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
            scr_out = c["regulatory"]["sources"]["screener"] or {}
            false_flags.append({"id": i, "scenario": l["scenario"], "tripwire": [h["category"] for h in c["regulatory"]["sources"]["tripwire"]],
                                "screener": [x["id"] for x in scr_out.get("categories", [])] or (["unspecified"] if scr_out.get("potential_regulatory") else [])})
        elif truth:
            fn += 1
            missed.append({"id": i, "scenario": l["scenario"], "categories": l["reg_categories"], "tags": l["tags"]})
        else:
            tn += 1
    positives = tp + fn
    secs = [t for c in cases.values() if c["regulatory"]["flagged"] and (t := model_seconds(c)["to_flag"]) is not None]
    model_only = sorted(model_seconds(c)["to_flag"] for c in cases.values()
                        if c["regulatory"]["flagged"] and not c["regulatory"]["sources"]["tripwire"] and model_seconds(c)["to_flag"])
    # Lanes: how flags would split if Compliance takes the two-lane option.
    lane = Counter()
    for i in ids:
        reg_i = cases[i]["regulatory"]
        if reg_i["flagged"]:
            lane[(reg_i.get("lane") or "review_48h", labels[i]["regulatory"])] += 1
    neg_n = max(fp + tn, 1)
    r_fp = lane[("review_48h", False)] / neg_n
    t_fp = lane[("tripwire_check", False)] / neg_n
    lanes = {
        "review_48h": {"regulatory": lane[("review_48h", True)], "not_regulatory": lane[("review_48h", False)]},
        "tripwire_check": {"regulatory": lane[("tripwire_check", True)], "not_regulatory": lane[("tripwire_check", False)]},
        # Monthly load depends on the real share of regulatory complaints, which only the pilot can measure.
        "monthly_load_by_share": {f"{int(sh * 100)}%": {
            "review_48h": round(MONTHLY_VOLUME * (sh * rate(lane[("review_48h", True)], positives) + (1 - sh) * r_fp)),
            "tripwire_check": round(MONTHLY_VOLUME * (sh * rate(lane[("tripwire_check", True)], positives) + (1 - sh) * t_fp))}
            for sh in (0.15, 0.25, round(positives / len(ids), 2))},
    }
    out["regulatory"] = {
        "positives": positives, "negatives": fp + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "recall": rate(tp, positives), "precision": rate(tp, tp + fp),
        "recall_tripwire_alone": rate(trip_tp, positives), "recall_screener_alone": rate(scr_tp, positives),
        "category_recall": {k: f"{cat_hit[k]}/{cat_total[k]}" for k in sorted(cat_total)},
        "lanes": lanes, "tripwire_terms": tripwire_terms(cases, labels),
        "missed": missed, "false_flags": false_flags,
        "flag_rate": rate(tp + fp, len(ids)), "projected_monthly_flags": round(MONTHLY_VOLUME * (tp + fp) / len(ids)),
        "median_seconds_to_flag": round(statistics.median(secs), 1) if secs else None,
        "flags_at_intake": sum(bool(c["regulatory"]["sources"]["tripwire"]) for c in cases.values() if c["regulatory"]["flagged"]),
        "model_only_flags": len(model_only),
        "model_only_median_seconds": round(statistics.median(model_only), 1) if model_only else None,
        "model_only_max_seconds": round(max(model_only), 1) if model_only else None,
        "max_seconds_to_flag": round(max(secs), 1) if secs else None,
    }

    # Routing
    exact = acceptable = routed = 0
    misroutes = []
    for i in ids:
        r = cases[i].get("route")
        if not r:
            continue
        routed += 1
        l = labels[i]
        exact += r["primary"] == l["primary_department"]
        ok = r["primary"] in l["acceptable_departments"]
        acceptable += ok
        if not ok:
            misroutes.append({"id": i, "scenario": l["scenario"], "predicted": r["primary"], "expected": l["acceptable_departments"]})
    out["routing"] = {"routed": routed, "exact": rate(exact, routed), "acceptable": rate(acceptable, routed), "misroutes": misroutes}

    # Facts and priority
    dims = [d["id"] for d in config.rubric()]
    agree = Counter()
    n_facts = 0
    pri_agree = Counter()
    ab_disagree = []
    bands = ["none", "under_100", "100_to_1000", "over_1000"]
    within_one = 0
    for i in ids:
        f = cases[i].get("facts")
        lf = {**labels[i]["facts"], "is_complaint": "non_complaint" not in labels[i]["tags"]}
        truth_pri = policy.evaluate_all(lf)
        if truth_pri["analyst_a"]["priority"] != truth_pri["analyst_b"]["priority"]:
            ab_disagree.append({"id": i, "scenario": labels[i]["scenario"], "A": truth_pri["analyst_a"]["priority"],
                                "B": truth_pri["analyst_b"]["priority"]})
        if not f:
            continue
        n_facts += 1
        for d in dims:
            agree[d] += f[d] == lf[d]
        a, b = f["financial_impact"], lf["financial_impact"]
        within_one += a == b or (a in bands and b in bands and abs(bands.index(a) - bands.index(b)) <= 1)
        pred_pri = cases[i]["priority"]
        for pid in truth_pri:
            pri_agree[pid] += pred_pri[pid]["priority"] == truth_pri[pid]["priority"]
    out["facts"] = {"cases": n_facts, "accuracy": {d: rate(agree[d], n_facts) for d in dims},
                    "financial_impact_within_one_band": rate(within_one, n_facts)}
    out["priority"] = {"agreement_with_label_priority": {p: rate(pri_agree[p], n_facts) for p in sorted(pri_agree)},
                       "analyst_disagreements_on_true_facts": len(ab_disagree), "disagreement_examples": ab_disagree}

    # Drafts
    drafts = [cases[i]["draft"] for i in ids if cases[i].get("draft")]
    flagged_standard = [i for i in ids if cases[i].get("draft") and cases[i]["regulatory"]["flagged"]
                        and cases[i]["draft"]["mode"] != "ACKNOWLEDGE ONLY"]
    hard = Counter(h for d in drafts for h in d["deterministic"]["hard_failures"])
    failed_checks = Counter(k for d in drafts for k, v in ((d.get("review") or {}).get("checks") or {}).items() if not v["pass"])
    out["drafts"] = {"drafted": len(drafts), "ready_for_analyst": sum(d["state"] == "ready_for_analyst" for d in drafts),
                     "needs_attention": sum(d["state"] == "needs_attention" for d in drafts),
                     "revised_once": sum(d["revisions"] for d in drafts),
                     "flagged_but_not_acknowledge_mode": flagged_standard,
                     "final_hard_failures": dict(hard), "final_reviewer_fails_by_check": dict(failed_checks)}

    # Adversarial
    inj = [i for i in ids if "injection" in labels[i]["tags"]]
    out["injection"] = {
        "cases": len(inj),
        "detected": sum(bool(cases[i]["injection_suspected"]) for i in inj),
        "regulatory_correct": sum(cases[i]["regulatory"]["flagged"] == labels[i]["regulatory"] for i in inj),
        "route_acceptable": sum(bool(cases[i].get("route")) and cases[i]["route"]["primary"] in labels[i]["acceptable_departments"] for i in inj),
        "draft_hard_failures": sum(bool(cases[i].get("draft")) and bool(cases[i]["draft"]["deterministic"]["hard_failures"]) for i in inj),
        "per_case": [{"id": i, "detected": cases[i]["injection_suspected"], "flagged": cases[i]["regulatory"]["flagged"],
                      "truth": labels[i]["regulatory"], "route": (cases[i].get("route") or {}).get("primary"),
                      "draft_state": (cases[i].get("draft") or {}).get("state")} for i in inj],
    }

    # Slices
    slices = {}
    for tag in sorted({t for l in labels.values() for t in l["tags"]}):
        sub = [i for i in ids if tag in labels[i]["tags"]]
        pos = [i for i in sub if labels[i]["regulatory"]]
        slices[tag] = {"n": len(sub), "reg_recall": rate(sum(cases[i]["regulatory"]["flagged"] for i in pos), len(pos)),
                       "route_acceptable": rate(sum(bool(cases[i].get("route")) and cases[i]["route"]["primary"] in labels[i]["acceptable_departments"] for i in sub), len(sub))}
    for ch in ("email", "web_form", "call_note"):
        sub = [i for i in ids if cases[i]["channel"] == ch]
        pos = [i for i in sub if labels[i]["regulatory"]]
        slices[f"channel:{ch}"] = {"n": len(sub), "reg_recall": rate(sum(cases[i]["regulatory"]["flagged"] for i in pos), len(pos)),
                                   "route_acceptable": rate(sum(bool(cases[i].get("route")) and cases[i]["route"]["primary"] in labels[i]["acceptable_departments"] for i in sub), len(sub))}
    out["slices"] = slices

    # Operations
    statuses = Counter(c["status"] for c in cases.values())
    failures = Counter(f"{f['stage']}:{f['reason']}" for c in cases.values() for f in c["failures"])
    costs = [c["usage"]["cost_usd"] for c in cases.values()]
    lat = [t for c in cases.values() if (t := model_seconds(c)["end_to_end"])]
    out["operations"] = {"statuses": dict(statuses), "failures": dict(failures),
                         "cost_per_complaint_usd": round(statistics.mean(costs), 4) if costs else None,
                         "projected_monthly_model_cost_usd": round(statistics.mean(costs) * MONTHLY_VOLUME) if costs else None,
                         "median_end_to_end_seconds": round(statistics.median(lat), 1) if lat else None,
                         "calls": sum(c["usage"]["calls"] for c in cases.values())}
    return out


# ----------------------------------------------------------------------- run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "replay", "live"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    complaints = load_jsonl(DATA / "complaints.jsonl")
    labels = {l["id"]: l for l in load_jsonl(DATA / "labels.jsonl")}
    crm = json.loads((DATA / "crm.json").read_text())
    if args.limit:
        complaints = complaints[: args.limit]
        labels = {c["id"]: labels[c["id"]] for c in complaints}
    assert complaints and len(labels) == len(complaints), "dataset missing or labels out of step"

    name = args.name or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    var = ROOT / "evals" / "runs" / name
    if var.exists():                     # a re-used run name starts clean, or logs would double-count
        import shutil
        shutil.rmtree(var)
    pipe = Pipeline(crm, var=var, mode=args.mode, cache_dir=CACHE)
    t0 = time.monotonic()
    cases = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pipe.process, c): c["id"] for c in complaints}
        for n, f in enumerate(as_completed(futs), 1):
            cases[futs[f]] = f.result()
            if n % 10 == 0 or n == len(futs):
                print(f"  {n}/{len(futs)} processed  ({time.monotonic() - t0:.0f}s)", flush=True)

    egress = [e for e in load_jsonl(var / "egress.jsonl") if "user" in e]   # failure records carry no payload
    originals = {cid: json.loads((var / "private" / f"{cid}.json").read_text())["original_text"] for cid in cases}
    for cid, c in cases.items():
        c["_original"] = originals[cid]      # in memory only, for the per-term tripwire table
    from evals.redaction_check import check as redaction_check
    holdout = ROOT / "data" / "holdout"
    spent = sum(call["cost_usd"] for c in cases.values() for call in c["calls"] if not call["cached"])
    result = {
        "run": name, "mode": args.mode, "model": pipe.llm.model, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complaints": len(cases), "wall_seconds": round(time.monotonic() - t0, 1), "spent_this_run_usd": round(spent, 2),
        "audit_chain": pipe.audit.verify(),
        "pii": scan_leaks(labels, egress, originals),
        "redaction_holdout": redaction_check(holdout) if holdout.exists() else None,
        **score(cases, labels),
    }
    out = ROOT / "evals" / "results" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    (ROOT / "evals" / "results" / "latest.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    from evals.report import write_evidence
    write_evidence(result)
    r, p = result["regulatory"], result["pii"]
    print(f"\nregulatory recall {r['recall']}  precision {r['precision']}  | routing acceptable {result['routing']['acceptable']}"
          f"  | PII leaked {p['total_leaked']}/{p['total_planted']} across {p['payloads_scanned']} payloads"
          f"  | spent ${result['spent_this_run_usd']}\nresults: {out}")


if __name__ == "__main__":
    main()
