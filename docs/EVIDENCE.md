# Evidence: how the prototype behaves on a synthetic month

Generated from `evals/results/month.json` (2026-10-09T02:50:22+00:00). Model `claude-opus-5-5`, run mode `replay`. 163 complaints. Re-run with `python3 -m evals.run_eval`.

The labels live in a sidecar file the pipeline never reads. The dataset was written by a separate agent that could see the taxonomy but not the detectors, prompts or tripwire terms. See `data/README.md`.

## At a glance

- **Regulatory recall 63/63.** Every category found. Each flag reached the Compliance queue before drafting began: 81 at intake via the tripwire, 25 more by the model screener in a median 4.8 s.
- **Precision 59.4%: the open decision.** 25 of the 43 false flags came from the tripwire alone, on complaints the model read as not regulatory. A two-lane queue for Compliance is proposed in §2.
- **Personal data: 0 of 159** in-text values reached the model on this month; **1 of 165** on a held-out month never used for tuning (§1).
- **Routing** defensible in 99.4%; **drafts** passed every check in 159 of 163; **injection attempts** handled correctly in 12 of 12.
- **Cost** $0.075 per complaint, about $105/month at current volume.

## 1. Customer data stays in the bank

Every outbound model payload is logged. The leak scan checked **901 payloads** across 163 complaints against every personal detail planted inside complaint text (**159**). Another 359 planted values sit in sender fields (from-address, form name, callback number); those fields are never part of any payload, by design.

| PII type | inside complaint text | reached a model |
|---|---:|---:|
| ACCOUNT_NUMBER | 11 | 0 |
| ADDRESS | 12 | 0 |
| CARD_NUMBER | 4 | 0 |
| CUSTOMER_NAME | 58 | 0 |
| DOB | 6 | 0 |
| EMAIL | 4 | 0 |
| PHONE | 22 | 0 |
| ROUTING_NUMBER | 3 | 0 |
| SSN | 4 | 0 |
| THIRD_PARTY_NAME | 35 | 0 |
| **total** | **159** | **0** |

**How much to trust that number.** The redactor was first written blind to the data. On its first run it left 8 of 159 in-text values readable on this month and 8 of 165 on a held-out month (same generator, different seed, so different people and numbers). Every miss was a name or an awkwardly written detail. The fixes were general: role cues accept dictionary-word names ("teller Tammy"), a name found once is redacted everywhere it recurs, spelled-out names (C-H-A-U-D-H-R-Y), filler words before a date of birth, line-by-line detection of capitals in call notes, and more street suffixes. Because those fixes were found on this month, its 0 is optimistic.
The held-out month was never used for tuning: **1 of 165** in-text values stay readable there (THIRD_PARTY_NAME 1). That is the honest estimate. The remaining miss is a full name in an all-capitals call note that is in no customer record. Over-redaction (ordinary words hidden as names) is 13 words across the held-out month. Reproduce with `python3 -m evals.redaction_check` (no model calls).

The residual is names with no pattern and no directory entry. Production closes it two ways: a named-entity model running on bank hardware in front of the gate, and running Claude inside Meridian's own cloud tenancy so redaction becomes a second layer rather than the only one.

Failure handling: when the egress gate finds residual PII, the call is not made and the complaint fails closed to manual triage. Unit tests disable the redactor entirely and confirm that zero calls leave and the complaint still reaches Compliance.

## 2. Regulatory complaints reach Compliance on arrival

| | |
|---|---|
| Regulatory complaints in set | 63 |
| Flagged (recall) | **63/63 = 100.0%** |
| Tripwire alone would have caught | 82.5% |
| Model screener alone would have caught | 100.0% |
| Non-regulatory complaints also flagged | 43/100 (precision 59.4%) |
| All flags, both lanes, at this month's mix | ~910 a month at 1,400 complaints (see lanes below) |
| Flags raised at intake by the tripwire, before any model call | 81 |
| Flags only the model raised: time from receipt to the Compliance queue | median 4.8 s, max 7.0 s (25 flags) |

**Precision is the open issue, and it is a decision for Compliance.** Splitting the flags by which check raised them:

| lane | what goes in it | truly regulatory | not regulatory |
|---|---|---:|---:|
| 48-hour review | model screener flagged it, or the screen could not run | 63 | 18 |
| tripwire check | only the tripwire fired; the model read it as not regulatory | 0 | 25 |

On this month every regulatory complaint was flagged by the model, and the tripwire-only lane held no regulatory complaints. The tripwire stays as a floor, because it is the check that still works if the model fails, drifts or is manipulated. But its hits do not need the same handling. The prototype shows Compliance both lanes; whether the tripwire lane is a daily glance or the full 48-hour process is her call.

Expected monthly load at 1,400 complaints. This depends on the real share of regulatory complaints, which the shadow pilot measures; this synthetic month is deliberately rich in them:

| regulatory share | 48-hour review | tripwire check |
|---|---:|---:|
| 15% | ~424/month (~20/business day) | ~298/month (~14/day) |
| 25% | ~539/month (~26/business day) | ~262/month (~12/day) |
| 39% | ~700/month (~33/business day) | ~214/month (~10/day) |

Tripwire terms ranked by false flags, for Compliance to narrow or keep (`config/tripwire.json`):

| term | false flags | true flags | true flags the model missed |
|---|---:|---:|---:|
| `\bfraud` | 9 | 7 | 0 |
| `my (elderly |\d+[- ]year[- ]old )?(mother|father|mom|dad|grandmother|grandfather|aunt|uncle)` | 4 | 2 | 0 |
| `\bdisput` | 4 | 6 | 0 |
| `\bunfair` | 3 | 0 | 0 |
| `stolen` | 3 | 1 | 0 |
| `\bscam` | 3 | 3 | 0 |
| `service charge` | 2 | 0 | 0 |
| `treated (me )?(differently|like)` | 2 | 1 | 0 |
| `\b(9|10|11) ?pm\b` | 2 | 1 | 0 |
| `monthly fee` | 1 | 0 | 0 |
| `payoff (amount|quote|letter|figure)` | 1 | 1 | 0 |
| `power of attorney` | 1 | 1 | 0 |

Category-level recall (was the right category named, not just a flag raised):

| category | found |
|---|---|
| collections_conduct | 6/6 |
| credit_reporting | 5/5 |
| error_resolution | 17/17 |
| fee_practices | 14/14 |
| privacy_incident | 8/8 |
| servicing_error | 9/9 |
| unfair_treatment | 9/9 |
| vulnerable_exploitation | 5/5 |

No regulatory complaint was missed.

<details><summary>43 false flags and which check raised them</summary>

- `C-0011` card_declined_fraud_block_travel: tripwire ['error_resolution'], screener -
- `C-0015` phishing_report_no_loss: tripwire ['error_resolution'], screener -
- `C-0016` mortgage_statement_format: tripwire -, screener ['servicing_error']
- `C-0017` compliment_branch_staff: tripwire ['vulnerable_exploitation'], screener -
- `C-0018` bereavement_joint_account_access: tripwire -, screener ['vulnerable_exploitation', 'unfair_treatment']
- `C-0019` phishing_report_no_loss: tripwire ['error_resolution'], screener -
- `C-0021` auto_payoff_letter_delay: tripwire -, screener ['servicing_error']
- `C-0023` app_redesign_complaint: tripwire ['unfair_treatment'], screener -
- `C-0024` deposit_hold_funds_unavailable: tripwire ['unfair_treatment', 'vulnerable_exploitation'], screener -
- `C-0025` credit_limit_decreased: tripwire -, screener ['credit_reporting']
- `C-0029` rewards_program_change: tripwire ['error_resolution'], screener -
- `C-0031` card_declined_fraud_block_travel: tripwire ['error_resolution'], screener -
- `C-0035` internal_transfer_error_no_funds_moved: tripwire ['collections_conduct'], screener -
- `C-0040` cryptic_merchant_descriptor: tripwire ['error_resolution'], screener -
- `C-0041` auto_payoff_letter_delay: tripwire ['servicing_error'], screener ['servicing_error']
- `C-0048` replacement_card_not_received: tripwire -, screener ['error_resolution']
- `C-0053` compliment_branch_staff: tripwire ['vulnerable_exploitation'], screener -
- `C-0057` app_redesign_complaint: tripwire ['error_resolution'], screener -
- `C-0060` branch_closure: tripwire ['unfair_treatment'], screener -
- `C-0062` card_declined_fraud_block_travel: tripwire ['error_resolution'], screener -
- `C-0064` deposit_hold_funds_unavailable: tripwire ['unfair_treatment'], screener ['unspecified']
- `C-0066` credit_limit_decreased: tripwire -, screener ['credit_reporting']
- `C-0080` loan_denied_appeal_request: tripwire ['unfair_treatment'], screener -
- `C-0089` marketing_opt_out_request: tripwire -, screener ['privacy_incident']
- `C-0091` overdraft_protection_question: tripwire ['fee_practices'], screener -
- `C-0094` bereavement_joint_account_access: tripwire -, screener ['unfair_treatment']
- `C-0096` collections_letter_question: tripwire -, screener ['servicing_error']
- `C-0100` online_banking_outage_rent_due: tripwire ['fee_practices'], screener -
- `C-0103` cryptic_merchant_descriptor: tripwire ['error_resolution'], screener -
- `C-0105` rewards_program_change: tripwire ['error_resolution'], screener ['unfair_treatment']
- `C-0107` heloc_draw_pending_review: tripwire -, screener ['servicing_error']
- `C-0109` compliment_card_team: tripwire ['error_resolution'], screener -
- `C-0110` courtesy_fee_waiver_request: tripwire ['fee_practices'], screener -
- `C-0116` card_declined_fraud_block_travel: tripwire ['error_resolution'], screener -
- `C-0130` call_center_hold_time: tripwire ['servicing_error'], screener -
- `C-0139` internal_transfer_error_no_funds_moved: tripwire ['collections_conduct'], screener -
- `C-0140` checks_printed_wrong_address: tripwire -, screener ['privacy_incident']
- `C-0142` deposit_hold_funds_unavailable: tripwire -, screener ['unspecified']
- `C-0145` courtesy_fee_waiver_request: tripwire ['error_resolution', 'fee_practices'], screener -
- `C-0154` poa_document_rejected: tripwire ['vulnerable_exploitation'], screener ['vulnerable_exploitation']
- `C-0159` cd_renewed_against_instructions: tripwire -, screener ['fee_practices']
- `C-0160` branch_closure: tripwire ['unfair_treatment'], screener -
- `C-0162` mobile_app_lockout: tripwire -, screener ['privacy_incident']

</details>

## 3. Routing

Primary department matches the label exactly in **95.1%** of 163 complaints and is a defensible owner in **99.4%**.

| complaint | scenario | routed to | acceptable |
|---|---|---|---|
| `C-0076` | address_change_request | card_services | deposit_ops, branch_contact_center |

## 4. "Serious": facts, not a verdict

The model extracts facts; the analysts' policy files turn facts into priority.

| fact | accuracy |
|---|---:|
| financial_impact | 92.0% |
| vulnerability_indicated | 96.3% |
| harm_ongoing | 85.3% |
| time_sensitive | 96.3% |
| escalation_threat | 99.4% |
| repeat_contact | 96.9% |
| financial_impact within one band | 92.0% |

Accuracy here means agreement with the labeler. Most disagreements fall where the rubric leaves room, for example whether an unreversed duplicate charge is harm that is still happening. That is the same ambiguity the senior analysts argue about, and the rubric file is where it gets settled. The `harm_ongoing` wording was clarified once, after a 12-complaint smoke run.

Priority agreement (priority computed from extracted facts vs. from labeled facts, same policy):

| policy | agreement |
|---|---:|
| Analyst A, money first | 90.2% |
| Analyst B, customer first | 84.0% |
| Interim, higher of A and B | 84.0% |

On the *true* facts, the two analyst policies disagree on **65 of 163** complaints. That list is the agenda for the 30-minute calibration session:

| complaint | scenario | Analyst A | Analyst B |
|---|---|---|---|
| `C-0001` | online_banking_outage_rent_due | P2 | P1 |
| `C-0002` | duplicate_merchant_charge | P1 | P2 |
| `C-0004` | undisclosed_paper_statement_fee_elderly | P3 | P1 |
| `C-0005` | replacement_card_not_received | P3 | P2 |
| `C-0008` | statements_mailed_to_old_address | P3 | P2 |
| `C-0009` | collection_calls_to_wrong_person | P3 | P2 |
| `C-0011` | card_declined_fraud_block_travel | P3 | P1 |
| `C-0013` | fee_on_account_sold_as_free | P3 | P1 |
| `C-0014` | extra_principal_not_applied | P1 | P2 |
| `C-0021` | auto_payoff_letter_delay | P3 | P2 |
| `C-0022` | bill_pay_not_received_by_payee | P1 | P2 |
| `C-0028` | late_payment_misreported | P3 | P2 |
| `C-0030` | closing_check_name_misspelled | P1 | P2 |
| `C-0033` | advertised_rate_not_honored | P2 | P3 |
| `C-0034` | teller_differential_treatment | P3 | P1 |

## 5. Drafts and sign-off

- Drafts prepared: 163. Passed every check: 159. Marked *needs attention* for the analyst: 4. Sent back once by the reviewer: 43.
- Flagged complaints drafted in anything but acknowledge-only mode: **0**.
- Deterministic hard failures left in final drafts: none.
- Reviewer checks still failing on final drafts, shown to the analyst: tone 2, no invented specifics 1, addresses each issue 1.
- Nothing is sent. The code has no outbound channel; a structural test fails the build if one appears. Approval needs a named analyst, and on flagged complaints Compliance must acknowledge first.

## 6. Prompt injection

12 complaints contain text aimed at an AI system. Detected: 12/12. Regulatory decision still correct: 12/12. Route acceptable: 12/12. Drafts with hard failures: 0.

## 7. Hard slices

| slice | n | regulatory recall | route acceptable |
|---|---:|---:|---:|
| buried_regulatory | 4 | 100.0% | 100.0% |
| call_shorthand | 35 | 100.0% | 100.0% |
| duplicate | 8 | 100.0% | 100.0% |
| hard_negative | 24 | n/a | 100.0% |
| injection | 12 | 100.0% | 100.0% |
| low_literacy | 4 | 100.0% | 100.0% |
| multi_issue | 9 | 100.0% | 100.0% |
| non_complaint | 23 | n/a | 95.7% |
| non_english | 4 | 100.0% | 100.0% |
| pii_edge | 14 | 100.0% | 92.9% |
| third_party_writer | 5 | 100.0% | 100.0% |
| channel:email | 64 | 100.0% | 100.0% |
| channel:web_form | 57 | 100.0% | 98.2% |
| channel:call_note | 42 | 100.0% | 100.0% |

## 8. Operations

- Outcomes: 163 awaiting analyst. Failures by stage: none.
- Model cost per complaint: $0.075 → about $105/month at 1,400 complaints.
- Median end-to-end time per complaint (including draft and review): 26.3 s. Model calls: 901.
- Audit chain: intact, 975 entries, head `4ed8df51c9dc4ebd…`.
