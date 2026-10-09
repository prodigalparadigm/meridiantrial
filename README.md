# Meridian Harbor — complaint intake prototype

A working prototype of a multi-agent complaint-intake assistant for a 60-branch regional bank. It reads every complaint (email, web form or call-center note) as it arrives, flags possible regulatory issues to Compliance within seconds, routes the complaint to the owning department, extracts the facts that set priority, and prepares a draft reply for an analyst to edit and sign.

It does not send anything to customers, and it does not replace the three analysts. The goal is the backlog and the 48-hour regulatory window. Why this is the right scope: [docs/SCOPING.md](docs/SCOPING.md). How it behaves on a synthetic month: [docs/EVIDENCE.md](docs/EVIDENCE.md). The Friday walkthrough: [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md).

## Run it

Requires Python 3.12 and the `anthropic` SDK. Nothing else: the tests, server and UI use only the standard library.

```bash
python3 -m unittest discover -s tests -t .        # controls: redaction, egress gate, no-send path, flag ratchet, sign-off, audit chain
python3 -m evals.run_eval --mode replay           # score the synthetic month offline from recorded model responses
python3 -m meridian.server --seed --port 8770     # load the month (offline replay) and open http://127.0.0.1:8770
```

With `ANTHROPIC_API_KEY` set, `python3 -m evals.run_eval` re-runs anything not yet recorded against Claude. New complaints typed into the workbench are processed live. Without a key, unrecorded complaints fail closed: they go to manual triage and into the Compliance queue as *unscreened*, which is the intended behavior.

## How it works

```
 complaint ─► intake ─► REDACT (local) ─► TRIPWIRE (local, Compliance-owned terms) ──────────┐
                              │                                                               │
                              ▼  redacted text only, through the EGRESS GATE                  ▼
               ┌──────────────┬──────────────┬──────────────┐                       regulatory flag
               │  Regulatory  │    Triage    │    Router    │   (in parallel)        (one-way: models
               │   screener   │   analyst    │              │                         can raise it,
               └──────┬───────┴──────┬───────┴──────┬───────┘                         only Compliance
                      │        facts + quotes  department                             can clear it)
                      │              ▼
                      │      POLICY FILE (analysts own) ─► priority
                      ▼
                 Drafter ─► Reviewer ─(send back once)─► analyst queue ─► named analyst approves
                (acknowledge-only if flagged)                              (after Compliance acknowledges
                                                                            any flag) ─► bank's existing
                                                                            outbound process
```

| Step | Kind | Job | Why it is built this way |
|---|---|---|---|
| Intake | code | Normalize three channels; match the customer in the CRM | Metadata seeds redaction and never goes to a model |
| Redactor | code, local | Replace PII with tokens like `⟦ACCOUNT_1⟧`; keep the vault locally | Data must not leave un-redacted |
| Egress gate | code | Re-scan every outbound payload; block and fail closed on residual PII | One choke point, no exceptions, every payload logged |
| Tripwire | code, local | Regex terms Compliance owns | A floor the model cannot lower |
| Regulatory screener | Claude | Possible regulatory issue? Which categories? Quote the evidence | Recall over precision; Compliance decides, the model only says "look" |
| Triage analyst | Claude | Summary, issues, six facts with quotes | Facts, not a verdict on "serious" |
| Router | Claude | Owning department from the catalog | Owner of the underlying problem, not the topic the customer picked |
| Policy | code | Facts → P1/P2/P3 per analyst policy | The bank defines "serious" in a file, not a prompt |
| Drafter | Claude | Reply for an analyst; acknowledge-only when flagged | Commits the bank to nothing |
| Reviewer | Claude + code | Nine checks, hard rules, one send-back | Catches what a hurried analyst might miss |

All five agents run on `claude-opus-5-5` with effort set per role (low for triage and routing, medium for screening, drafting and review), structured JSON output, and prompt caching on the system prompts. Complaint text is framed as material to analyze, never as instructions. A refusal or outage fails closed to a person and never falls back to a different model, because a bank's model-risk process validates one specific model.

## What the compliance officer can check herself

- **Nothing can be sent.** No module imports a mail, HTTP-client or messaging library, and only `meridian/llm.py` imports the model SDK. `tests/test_controls.py::NoSendPath` enforces both.
- **What left the bank.** Every outbound payload is in `var/egress.jsonl`, and the workbench shows them per complaint ("What left the bank").
- **The leak scan.** The evaluation checks every logged payload against every PII value planted in the test data. See [EVIDENCE §1](docs/EVIDENCE.md).
- **Who did what.** `var/audit.jsonl` is hash-chained, so editing, deleting or reordering an entry breaks verification. The Compliance tab has a *Verify audit chain* button.
- **What she controls.** [config/compliance_taxonomy.json](config/compliance_taxonomy.json) and [config/tripwire.json](config/tripwire.json) are plain files, and the prompts read them verbatim.

## Repository

```
config/      stakeholder-owned files: departments, compliance taxonomy, tripwire terms, severity rubric, priority policies
meridian/    pipeline: intake, redact, gate, tripwire, llm (only model access), agents/, policy, audit, pipeline, server
ui/          the workbench (one HTML file, no external assets)
data/        synthetic dataset generator, verifier, and its README (written blind to the detectors)
evals/       run_eval.py (scoring), report.py (renders docs/EVIDENCE.md), results/, llm_cache/ (recorded responses, redacted)
tests/       unittest suite with erosion checks: each safety test is shown to fail when its control is removed
docs/        SCOPING.md, EVIDENCE.md, WALKTHROUGH.md
```

## Not production

Synthetic data only. No SSO or roles: the UI asks for a name. No integration with the bank's mailbox, CRM or case system. Name detection uses the customer directory plus rules; production would add a named-entity model running locally, or run Claude inside Meridian's own cloud tenancy (Amazon Bedrock or Google Vertex AI) so complaint text never crosses to an outside API at all. The policies for Analyst A and Analyst B are illustrative reconstructions until the two analysts rank real complaints.
