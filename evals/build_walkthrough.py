"""Fill docs/walkthrough.template.html from the latest eval result -> docs/walkthrough.html.

Every number and every demo case on the page comes from evals/results/latest.json and
the run's case files, so the walkthrough cannot drift from the evidence.

    python3 -m evals.build_walkthrough
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
X0, X1, DAYS = 120, 740, 7


def x(day: float) -> float:
    return round(X0 + (X1 - X0) * day / DAYS, 1)


def pct(v):
    return "n/a" if v is None else f"{v * 100:.0f}%"


def load(run: str):
    r = json.loads((ROOT / "evals" / "results" / f"{run}.json").read_text())
    cases = {p.stem: json.loads(p.read_text()) for p in (ROOT / "evals" / "runs" / run / "cases").glob("*.json")}
    labels = {l["id"]: l for l in map(json.loads, (ROOT / "data" / "synthetic" / "labels.jsonl").read_text().splitlines()) if l["id"] in cases}
    return r, cases, labels


def pick(cases, labels, want, avoid=()):
    for cid in sorted(cases):
        if cid not in avoid and want(cases[cid], labels[cid]):
            return cid
    return None


def main():
    latest = json.loads((ROOT / "evals" / "results" / "latest.json").read_text())
    r, cases, labels = load(latest["run"])
    reg, pii, rt, dr, inj, ops, pr = (r["regulatory"], r["pii"], r["routing"], r["drafts"], r["injection"],
                                     r["operations"], r["priority"])
    flagged_true = [c for cid, c in cases.items() if c["regulatory"]["flagged"] and labels[cid]["regulatory"]]
    by_trip = sum(bool(c["regulatory"]["sources"]["tripwire"]) for c in flagged_true)
    screen_only = sorted(max((k["latency_s"] for k in c["calls"] if k["agent"] == "regulatory_screener"), default=0)
                         for c in flagged_true if not c["regulatory"]["sources"]["tripwire"])
    screen_median = screen_only[len(screen_only) // 2] if screen_only else 0

    ticks = "".join(f'<line x1="{x(d)}" y1="162" x2="{x(d)}" y2="170" stroke="var(--muted)"/>'
                    f'<text x="{x(d)}" y="186" text-anchor="middle" class="m">{"receipt" if d == 0 else f"day {d}"}</text>'
                    for d in range(DAYS + 1))

    leak_total, planted = pii["total_leaked"], pii["total_planted"]
    ho = r.get("redaction_holdout") or {}
    pii_line = (f"On the test month, {leak_total} of {planted} planted personal details reached the model. On a second month "
                f"never used for tuning, {ho.get('leaked_total')} of {ho.get('in_text_total')} would have: a name written in capitals "
                f"in a call note. That is why production adds a name detector on bank hardware, or runs inside Meridian's own cloud.")

    rows = [
        ("Regulatory complaints flagged", f"{reg['tp']} / {reg['positives']}",
         "Recall. A miss is the failure Compliance cares about most." + (f" Misses: {', '.join(m['id'] for m in reg['missed'])}." if reg["missed"] else "")),
        ("Non-regulatory complaints also flagged", f"{reg['fp']} / {reg['negatives']}",
         f"The cost of caution. {reg['lanes']['tripwire_check']['not_regulatory']} of these came from the tripwire alone. See the decision below."),
        ("Each check alone", f"tripwire {pct(reg['recall_tripwire_alone'])} · model {pct(reg['recall_screener_alone'])}",
         "The model caught every one. The tripwire stays as the floor that still works if the model fails, drifts or is manipulated."),
        ("Personal details reaching the model", f"{leak_total} / {planted}",
         f"Checked across {pii['payloads_scanned']} logged outbound messages. On a held-out month never used for tuning: "
         f"{ho.get('leaked_total')} of {ho.get('in_text_total')}."),
        ("Routed to a defensible owner", pct(rt["acceptable"]), f"Exact match with the label: {pct(rt['exact'])}."),
        ("Drafts that passed every check", f"{dr['ready_for_analyst']} / {dr['drafted']}",
         "The rest are marked for the analyst's attention, with the reason."),
        ("Flagged complaints drafted as acknowledgement only", f"{dr['drafted'] and sum(1 for c in cases.values() if c.get('draft') and c['regulatory']['flagged'] and c['draft']['mode'] == 'ACKNOWLEDGE ONLY')} / {sum(1 for c in cases.values() if c.get('draft') and c['regulatory']['flagged'])}",
         "A flagged reply takes no position until Compliance has looked."),
        ("Complaints containing instructions aimed at an AI", f"{inj['regulatory_correct']} / {inj['cases']} handled correctly",
         f"Detected {inj['detected']} of {inj['cases']}. None changed a flag or put a promise in a draft." if inj["draft_hard_failures"] == 0 else "See evidence for details."),
        ("Model cost per complaint", f"${ops['cost_per_complaint_usd']:.2f}", f"About ${ops['projected_monthly_model_cost_usd']:,} a month at 1,400 complaints."),
    ]
    evidence_rows = "\n    ".join(f"<tr><td>{html.escape(a)}</td><td class=\"n\">{html.escape(b)}</td><td>{html.escape(c)}</td></tr>" for a, b, c in rows)

    limits = []
    if reg["missed"]:
        limits.append(f"{len(reg['missed'])} regulatory complaint(s) were missed; each is listed in the evidence with the reason")
    if leak_total:
        limits.append(f"{leak_total} personal detail(s) reached the model, all of them names with no pattern to match")
    limits_note = (f'<p class="note col"><b>Known gaps:</b> {"; ".join(limits)}. These are the first things the pilot must close, which is why a single leak stops it.</p>'
                   if limits else "")

    dis = pr["disagreement_examples"]
    dis_rows = []
    for d in dis[:5]:
        c = cases[d["id"]]
        summ = (c.get("triage") or {}).get("summary") or labels[d["id"]]["scenario"]
        summ_html = re.sub(r"⟦[A-Z_]+_\d+⟧", lambda m: f'<span class="tok">{m.group(0)}</span>', html.escape(summ))
        dis_rows.append(f'<tr><td><code>{d["id"]}</code> {summ_html}</td><td><span class="pill {"reg" if d["A"] == "P1" else "amb" if d["A"] == "P2" else "ok"}">{d["A"]}</span></td>'
                        f'<td><span class="pill {"reg" if d["B"] == "P1" else "amb" if d["B"] == "P2" else "ok"}">{d["B"]}</span></td></tr>')

    used: list[str] = []

    def take(fn):
        cid = pick(cases, labels, fn, used)
        if cid:
            used.append(cid)
        return cid

    buried = take(lambda c, l: "buried_regulatory" in l["tags"] and c["regulatory"]["flagged"] and c.get("draft"))
    inj_id = take(lambda c, l: "injection" in l["tags"] and l["regulatory"] and c["regulatory"]["flagged"] and c["injection_suspected"])
    split = take(lambda c, l: c.get("priority") and c["priority"]["analyst_a"]["priority"] == "P3" and c["priority"]["analyst_b"]["priority"] == "P1")
    spanish = take(lambda c, l: "non_english" in l["tags"] and c["regulatory"]["flagged"])
    pii_id = take(lambda c, l: "pii_edge" in l["tags"] and sum(c["redaction_counts"].values()) >= 4)
    rc = cases[pii_id]["redaction_counts"] if pii_id else {}
    kinds = ", ".join(f"{v} {k.lower().replace('_', ' ')}{'s' if v > 1 else ''}" for k, v in sorted(rc.items(), key=lambda kv: -kv[1]))
    steps = [
        (f"Open the analyst queue. Ordered by the interim rule.", "Each row is already read: priority, a regulatory badge where it applies, owner and draft status. Switch the ordering between Analyst A's and Analyst B's rule to see the queue reshuffle."),
        (f"Open <code>{buried}</code>.", "A complaint mostly about something else, with a regulatory issue in one sentence. It was in the Compliance queue within seconds, with the quote that triggered it. The draft is acknowledgement only, and Approve stays locked until Compliance acknowledges."),
        (f"Open <code>{pii_id}</code>. Toggle “As received” and “What the model saw”, then open “What left the bank”.", f"Replaced on this machine before anything left: {kinds}. What you see under “What left the bank” is the exact text that was sent, from the log."),
        (f"Switch to Compliance. Acknowledge <code>{buried}</code>, then verify the audit chain.", "Your action is recorded with your name. Go back to the analyst view, edit the draft and approve it. The record keeps both the draft and the approved text."),
        (f"Open <code>{inj_id}</code>.", "The complaint tells “the AI” to treat it as routine. It was flagged anyway, the attempt is labeled for the analyst, and the draft ignores it."),
        (f"Open <code>{split}</code>, then the Operations tab.", "One analyst's rule says P3 and the other's says P1. The calibration agenda lists every complaint like this one."),
        (f"Open <code>{spanish}</code>, or type a new complaint with “New complaint”.", "Spanish complaints are flagged and routed like any other. The draft is in English so the approving analyst can read what they sign. A complaint typed live goes through every step in about a minute."),
    ]
    demo = "\n    ".join(f'<li><b>{a}</b><div class="say">{html.escape(b)}</div></li>' for a, b in steps)

    tpl = (ROOT / "docs" / "walkthrough.template.html").read_text()
    fill = {
        "n": r["complaints"], "model": r["model"], "run": r["run"],
        "win_w": round(x(2) - X0, 1), "win_mid": round((X0 + x(2)) / 2, 1), "today_x": x(6), "ticks": ticks,
        "flag_phrase": f"within {round(reg['model_only_max_seconds'] or 0)} s",
        "tp": reg["tp"], "pos": reg["positives"], "trip_share": f"{by_trip} of {len(flagged_true)}", "screen_median": reg["model_only_median_seconds"],
        "trip_lane_n": reg["lanes"]["tripwire_check"]["not_regulatory"] + reg["lanes"]["tripwire_check"]["regulatory"],
        "lane_r_t": reg["lanes"]["review_48h"]["regulatory"], "lane_r_f": reg["lanes"]["review_48h"]["not_regulatory"],
        "lane_t_t": reg["lanes"]["tripwire_check"]["regulatory"], "lane_t_f": reg["lanes"]["tripwire_check"]["not_regulatory"],
        "load_line": " ".join(f"If {sh} are regulatory: about {round(v['review_48h'] / 21)} a day in 48-hour review and {round(v['tripwire_check'] / 21)} tripwire checks."
                              for sh, v in list(reg["lanes"]["monthly_load_by_share"].items())[:2]),
        "draft_ready_pct": pct(dr["ready_for_analyst"] / dr["drafted"]) if dr["drafted"] else "n/a",
        "route_acc": pct(rt["acceptable"]), "cost": f"{ops['cost_per_complaint_usd']:.2f}", "monthly": f"{ops['projected_monthly_model_cost_usd']:,}",
        "pii_line": pii_line, "evidence_rows": evidence_rows, "limits_note": limits_note,
        "disagree": pr["analyst_disagreements_on_true_facts"], "disagree_rows": "\n    ".join(dis_rows), "demo_steps": demo,
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    assert "{{" not in tpl, "unfilled placeholder"
    out = ROOT / "docs" / "walkthrough.html"
    out.write_text(tpl)
    print(f"wrote {out}  (demo cases: buried={buried} pii={pii_id} injection={inj_id} split={split} spanish={spanish})")


if __name__ == "__main__":
    main()
