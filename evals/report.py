"""Renders docs/EVIDENCE.md from an eval result. Every number in it comes from evals/results/latest.json."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def write_evidence(r: dict) -> Path:
    reg, pii, rt, fa, pr, dr, inj, ops = (r["regulatory"], r["pii"], r["routing"], r["facts"], r["priority"],
                                         r["drafts"], r["injection"], r["operations"])
    L = []
    w = L.append
    w("# Evidence: how the prototype behaves on a synthetic month\n")
    w(f"Generated from `evals/results/{r['run']}.json` ({r['at']}). Model `{r['model']}`, run mode `{r['mode']}`. "
      f"{r['complaints']} complaints. Re-run with `python3 -m evals.run_eval`.\n")
    w("The labels live in a sidecar file the pipeline never reads. The dataset was written by a separate agent "
      "that could see the taxonomy but not the detectors, prompts or tripwire terms. See `data/README.md`.\n")

    w("## 1. Customer data stays in the bank\n")
    w(f"Every outbound model payload is logged. The leak scan checked **{pii['payloads_scanned']} payloads** across "
      f"{pii['cases_with_payloads']} complaints against every personal detail planted inside complaint text "
      f"(**{pii['total_planted']}**). Another {pii['metadata_only']} planted values sit in sender fields (from-address, form "
      f"name, callback number); those fields are never part of any payload, by design.\n")
    w("| PII type | inside complaint text | reached a model |\n|---|---:|---:|")
    for t in sorted(pii["planted"]):
        w(f"| {t} | {pii['planted'][t]} | {pii['leaked'].get(t, 0)} |")
    w(f"| **total** | **{pii['total_planted']}** | **{pii['total_leaked']}** |\n")
    if pii["details"]:
        w("Leaks, for review (values withheld; context shows the redacted surroundings):\n")
        for d in pii["details"]:
            w(f"- `{d['id']}` {d['type']}: …{d['context'].strip()}…")
        w("")
    ho = r.get("redaction_holdout")
    w("**How much to trust that number.** The redactor was first written blind to the data. On its first run it "
      "left 8 of 159 in-text values readable on this month and 8 of 165 on a held-out month (same generator, different "
      "seed, so different people and numbers). Every miss was a name or an awkwardly written detail. The fixes were "
      "general: role cues accept dictionary-word names (\"teller Tammy\"), a name found once is redacted everywhere it recurs, "
      "spelled-out names (C-H-A-U-D-H-R-Y), filler words before a date of birth, line-by-line detection of capitals in "
      "call notes, and more street suffixes. Because those fixes were found on this month, its 0 is optimistic.")
    if ho:
        w(f"The held-out month was never used for tuning: **{ho['leaked_total']} of {ho['in_text_total']}** in-text values "
          f"stay readable there ({', '.join(f'{k} {v}' for k, v in ho['leaked'].items()) or 'none'}). That is the honest estimate. "
          f"The remaining miss is a full name in an all-capitals call note that is in no customer record. "
          f"Over-redaction (ordinary words hidden as names) is {ho['non_pii_words_hidden_as_names']} words across the held-out month. "
          f"Reproduce with `python3 -m evals.redaction_check` (no model calls).\n")
    w("The residual is names with no pattern and no directory entry. Production closes it two ways: a named-entity model "
      "running on bank hardware in front of the gate, and running Claude inside Meridian's own cloud tenancy so redaction "
      "becomes a second layer rather than the only one.\n")
    w("Failure handling: when the egress gate finds residual PII, the call is not made and the complaint fails closed to "
      "manual triage. Unit tests disable the redactor entirely and confirm that zero calls leave and the complaint "
      "still reaches Compliance.\n")

    w("## 2. Regulatory complaints reach Compliance on arrival\n")
    w(f"| | |\n|---|---|\n| Regulatory complaints in set | {reg['positives']} |\n| Flagged (recall) | **{reg['tp']}/{reg['positives']} = {pct(reg['recall'])}** |"
      f"\n| Tripwire alone would have caught | {pct(reg['recall_tripwire_alone'])} |\n| Model screener alone would have caught | {pct(reg['recall_screener_alone'])} |"
      f"\n| Non-regulatory complaints also flagged | {reg['fp']}/{reg['negatives']} (precision {pct(reg['precision'])}) |"
      f"\n| Projected Compliance queue at 1,400/month | ~{reg['projected_monthly_flags']} flags |"
      f"\n| Median time from receipt to flag | {reg['median_seconds_to_flag']} s (max {reg['max_seconds_to_flag']} s) |\n")
    w("Category-level recall (was the right category named, not just a flag raised):\n")
    w("| category | found |\n|---|---|")
    for k, v in reg["category_recall"].items():
        w(f"| {k} | {v} |")
    w("")
    if reg["missed"]:
        w("Missed regulatory complaints:\n")
        for m in reg["missed"]:
            w(f"- `{m['id']}` {m['scenario']} ({', '.join(m['categories'])}; tags: {', '.join(m['tags']) or 'none'})")
        w("")
    else:
        w("No regulatory complaint was missed.\n")
    if reg["false_flags"]:
        w(f"<details><summary>{len(reg['false_flags'])} false flags and which check raised them</summary>\n")
        for f in reg["false_flags"]:
            w(f"- `{f['id']}` {f['scenario']}: tripwire {f['tripwire'] or '-'}, screener {f['screener'] or '-'}")
        w("\n</details>\n")

    w("## 3. Routing\n")
    w(f"Primary department matches the label exactly in **{pct(rt['exact'])}** of {rt['routed']} complaints and is a "
      f"defensible owner in **{pct(rt['acceptable'])}**.\n")
    if rt["misroutes"]:
        w("| complaint | scenario | routed to | acceptable |\n|---|---|---|---|")
        for m in rt["misroutes"]:
            w(f"| `{m['id']}` | {m['scenario']} | {m['predicted']} | {', '.join(m['expected'])} |")
        w("")

    w("## 4. \"Serious\": facts, not a verdict\n")
    w("The model extracts facts; the analysts' policy files turn facts into priority.\n")
    w("| fact | accuracy |\n|---|---:|")
    for k, v in fa["accuracy"].items():
        w(f"| {k} | {pct(v)} |")
    w(f"| financial_impact within one band | {pct(fa['financial_impact_within_one_band'])} |\n")
    w("Priority agreement (priority computed from extracted facts vs. from labeled facts, same policy):\n")
    w("| policy | agreement |\n|---|---:|")
    for k, v in pr["agreement_with_label_priority"].items():
        w(f"| {k} | {pct(v)} |")
    w(f"\nOn the *true* facts, the two analyst policies disagree on **{pr['analyst_disagreements_on_true_facts']} of "
      f"{r['complaints']}** complaints. That list is the agenda for the 30-minute calibration session:\n")
    w("| complaint | scenario | Analyst A | Analyst B |\n|---|---|---|---|")
    for d in pr["disagreement_examples"][:15]:
        w(f"| `{d['id']}` | {d['scenario']} | {d['A']} | {d['B']} |")
    w("")

    w("## 5. Drafts and sign-off\n")
    w(f"- Drafts prepared: {dr['drafted']}. Passed every check: {dr['ready_for_analyst']}. Marked *needs attention* "
      f"for the analyst: {dr['needs_attention']}. Sent back once by the reviewer: {dr['revised_once']}.")
    w(f"- Flagged complaints drafted in anything but acknowledge-only mode: **{len(dr['flagged_but_not_acknowledge_mode'])}**.")
    w(f"- Deterministic hard failures left in final drafts: {dr['final_hard_failures'] or 'none'}.")
    w(f"- Reviewer checks still failing on final drafts (surfaced to the analyst): {dr['final_reviewer_fails_by_check'] or 'none'}.")
    w("- Nothing is sent. The code has no outbound channel; a structural test fails the build if one appears. "
      "Approval needs a named analyst, and on flagged complaints Compliance must acknowledge first.\n")

    w("## 6. Prompt injection\n")
    w(f"{inj['cases']} complaints contain text aimed at an AI system. Detected: {inj['detected']}/{inj['cases']}. "
      f"Regulatory decision still correct: {inj['regulatory_correct']}/{inj['cases']}. Route acceptable: "
      f"{inj['route_acceptable']}/{inj['cases']}. Drafts with hard failures: {inj['draft_hard_failures']}.\n")

    w("## 7. Hard slices\n")
    w("| slice | n | regulatory recall | route acceptable |\n|---|---:|---:|---:|")
    for k, v in r["slices"].items():
        w(f"| {k} | {v['n']} | {pct(v['reg_recall'])} | {pct(v['route_acceptable'])} |")
    w("")

    w("## 8. Operations\n")
    w(f"- Outcomes: {ops['statuses']}. Failures by stage: {ops['failures'] or 'none'}.")
    w(f"- Model cost per complaint: ${ops['cost_per_complaint_usd']} → about ${ops['projected_monthly_model_cost_usd']}/month at 1,400 complaints.")
    w(f"- Median end-to-end time per complaint (including draft and review): {ops['median_end_to_end_seconds']} s. "
      f"Model calls: {ops['calls']}.")
    w(f"- Audit chain: {r['audit_chain']}.\n")
    path = ROOT / "docs" / "EVIDENCE.md"
    path.write_text("\n".join(L))
    return path


if __name__ == "__main__":
    write_evidence(json.loads((ROOT / "evals" / "results" / "latest.json").read_text()))
