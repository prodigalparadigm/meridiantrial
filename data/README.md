# Synthetic complaint evaluation set (Meridian Harbor Bank)

Ground truth for grading the complaint-intake prototype. Everything here is fictional: the people, phone numbers (all `555-01xx`), email domains (`example.com/.net/.org`), SSNs (`9xx-`), card numbers (Luhn-valid with `400000`/`555500` test prefixes), routing numbers (built to fail the ABA checksum), and addresses (in invented towns).

| File | What it is |
|---|---|
| `synthetic/complaints.jsonl` | 163 complaints. Each record holds only what the bank receives: `id`, `channel`, `received_at`, `payload` |
| `synthetic/labels.jsonl` | Sidecar ground truth for the same ids: scenario, customer_id, regulatory categories, departments, severity facts, PII spans, tags, notes |
| `synthetic/crm.json` | The bank's customer directory: 199 customers (153 complainants, 46 who never complain) |
| `generate.py` | Builds all three files. Standard library only, fixed seed, byte-identical on every run |
| `verify_dataset.py` | Checks everything below and exits non-zero on any failure |

```
python3 generate.py            # writes synthetic/
python3 verify_dataset.py      # validates + regenerates twice to prove determinism
```

## How it was built

- **Archetypes, not free text.** There are 94 complaint scenarios. Each one is a Python function with 1–3 phrasings per channel. It fills in slots (names, amounts, dates, branches, merchants) and **sets every label in the same code path that writes the matching text**. If a sentence says "I'm 84 and on a fixed income", the same line sets `vulnerability_indicated`. If an amount is inserted, it is added to the at-stake total that determines `financial_impact`.
- **PII by construction.** Every PII string goes in through a helper that records its type. After the payload is assembled, each recorded string is located again in the final text. That includes the upper-cased version when a call note is written in ALL CAPS. Generation aborts if a string has no standalone occurrence.
- **Dates are relative to `received_at`.** Past events fall before arrival and deadlines fall after it. Branch visits never land on a Sunday or Labor Day (Sept 7).
- **Duplicates share state.** The two halves of a duplicate pair reuse the same person, amounts and dates. The second arrives 1–5 days later on another channel and says so.
- **CRM traps.** Three complainants share a full name with a different CRM customer. One complainant shares an address with a household member who is also a customer. One non-customer complainant has the same name as a CRM customer (correct `customer_id` is `null`). Non-customer senders never match a CRM email or phone.
- **The plan is explicit.** `PLAN` in `generate.py` lists all 163 records (archetype, channel, options). That is how the composition is controlled.

## Composition

**Channel**

| email | web_form | call_note |
|---|---|---|
| 64 (39.3%) | 57 (35.0%) | 42 (25.8%) |

**Regulatory:** 63 of 163 records (38.7%) are regulatory-positive. Ten records carry two categories.

| category | records |
|---|---|
| error_resolution | 17 |
| fee_practices | 14 |
| unfair_treatment | 9 |
| credit_reporting | 5 |
| collections_conduct | 6 |
| privacy_incident | 8 |
| servicing_error | 9 |
| vulnerable_exploitation | 5 |

**Primary department.** 80 records list more than one acceptable department.

| department | records |
|---|---|
| deposit_ops | 28 |
| card_services | 21 |
| fraud_disputes | 19 |
| consumer_lending | 9 |
| mortgage_servicing | 16 |
| digital_banking | 21 |
| branch_contact_center | 27 |
| collections | 13 |
| privacy_office | 9 |

**Tags.** There are 151 core records and 12 adversarial ones.

| tag | records | tag | records |
|---|---|---|---|
| hard_negative | 24 | pii_edge | 14 |
| non_complaint | 23 | injection | 12 |
| call_shorthand | 35 | multi_issue | 9 |
| duplicate | 8 (4 pairs) | third_party_writer | 5 |
| buried_regulatory | 4 | non_english (Spanish) | 4 |
| low_literacy | 4 | | |

**Severity facts.** These vary independently.

- `financial_impact`: none 88, under_100 24, 100_to_1000 19, over_1000 27, unknown 5.
- Boolean facts, number of records where true: vulnerable 29, ongoing 58, time-sensitive 27, escalation 18, repeat 18.
- Four planted disagreement cases:
  - (a) vulnerable customer with little money at stake: 15 records, e.g. a few-dollar undisclosed statement fee on a 93-year-old.
  - (b) over $1,000 at stake, no threat, not vulnerable, no deadline: 7 records, e.g. a $104k CD renewed against instructions, a calm payoff refund.
  - (c) time-sensitive with little or no money: 11 records, e.g. a $15 expedite fee before a flight.
  - (d) threatening over something trivial: 6 records, e.g. "closing every account and calling Channel 8" over a broken pen.

**PII entries:** CUSTOMER_NAME 194, EMAIL 125, PHONE 120, THIRD_PARTY_NAME 39, ADDRESS 12, ACCOUNT_NUMBER 11, DOB 6, CARD_NUMBER 4, SSN 4, ROUTING_NUMBER 3.

## Labeling conventions (read before grading)

- **CUSTOMER_NAME** is the person the complaint is about: the account holder, or the complainant when they are not a customer. When someone writes on behalf of the account holder (an adult child, a spouse, a granddaughter), the writer's name is **THIRD_PARTY_NAME**, and `customer_id` is the account holder.
- **PII strings are listed once per distinct form.** "Margaret Okafor", "Margaret", "M. Okafor" and "OKAFOR, MARGARET" are four entries. A misspelled name on a check and a letter-by-letter spelling ("C-H-A-U-D-H-R-Y") are still CUSTOMER_NAME.
- **Not labeled as PII:**
  - last-4 fragments ("card ending 4821", the `account_last4` field);
  - branch, merchant and town names;
  - case and confirmation numbers;
  - a phishing URL.
- **Labeled as PII:**
  - the bank's own routing number (as ROUTING_NUMBER);
  - a scammer's phone number.
- **financial_impact** is the sum of money at stake. That means:
  - the amounts in dispute;
  - fees charged;
  - the customer's own funds held, missing or owed;
  - for hardship and collections complaints, the payment or past-due amount at issue.

  Amounts that are only mentioned do not count (a $500 transfer that failed with nothing moved is `none`). The bands are: under 100; 100 to 1,000 inclusive; over 1,000. The value is `unknown` when money is clearly involved but no figure is given.
- **harm_ongoing** is true when the harm is still happening:
  - the customer's own money is missing, held or owed and has not come back;
  - access is locked;
  - charges recur;
  - contact (calls, letters) continues.

  A one-time fee that has already been charged is not ongoing.
- **repeat_contact** means a previous complaint or contact about the same problem. An earlier *instruction* does not count. Examples: the address change that statements then ignored, or a CD maturity request.
- **vulnerability_indicated** follows the rubric literally. That includes a bereavement mentioned inside a compliment. Writing in Spanish is not by itself counted as limited English. Only an explicit statement is, which is the case in 3 of the 4 Spanish records.
- **escalation_threat**:
  - true for "cancel the card" and "close my accounts";
  - false for "I'll sue! ...just kidding";
  - false for "CFPB" when it appears only inside injected text.
- **Regulatory** applies the taxonomy descriptions word for word, including the five categories marked *proposed*. Issues outside the taxonomy are `regulatory: false`. Examples: deposit holds, a cashier's check with the name misspelled, a CD renewed against instructions, a branch closing.
- **Injection records** are labeled for the real complaint underneath and ignore the injected instruction. One injection (C-0113) sits on top of a non-regulatory complaint and tries to force a regulatory flag.

**Gray calls** are explained in each record's `notes`. The main ones:

- **Bereavement account lockouts** (C-0018, C-0094): `regulatory: false`, but an analyst could argue the bank's handling "put a vulnerable customer at risk".
- **Romance-scam wire hold** (C-0131): labeled `unfair_treatment` because she says she was questioned over her age and sex, even though the hold protected her.
- **POA refused twice** (C-0154): labeled non-regulatory.
- **Payment amounts counted as "at stake"** in hardship and collections cases.
- **Rent that can't be sent during an outage** counted as unavailable funds.

## What this dataset does NOT cover

- **Scale and time.** It has 163 records, against roughly 1,400 a month in production, and covers a single month in a single timezone. There is no seasonality and no realistic arrival bursts. The volume mix is planned, not sampled from real traffic.
- **Message realism gaps.**
  - No attachments, images, or HTML email.
  - No long reply chains, apart from one fake "forwarded" header.
  - No auto-replies, spam, or out-of-office messages.
  - No legal disclaimers in signatures.
  - Subject lines never contain PII.
  - Call notes are CSR summaries, not transcripts.
- **Languages.** Spanish only (4 records). No other languages, no non-Latin scripts, and no mid-sentence code-switching.
- **Products.** Retail only. No business banking, trust, wealth, safe-deposit, or international wires beyond passing mentions.
- **Regulation.** Labels cover only the 8 taxonomy categories. Nothing separately labels Reg CC holds, SCRA, flood insurance, BSA/AML, or OFAC. Labels say whether a complaint *raises* a category, not whether the customer is legally right.
- **PII adversaries.** Covered: split, dashed or spoken numbers, "at/dot" emails, inline and multi-line addresses, an SSN without dashes. Not covered: homoglyphs, encodings, PII inside images, deliberately misspelled emails.
- **Injection coverage.** 12 single-message attacks. No multi-turn attacks, no injection hidden in CRM data or attachments, no non-English injection.
- **Names.** The pool is diverse but invented. No mononyms, generational suffixes (Jr./III), or professional titles beyond Mr./Ms./Mrs.
- **CRM.** No SSNs, no joint-ownership links, no contact history. Customer matching is ambiguous only in the traps listed above.
- **Duplicates** are clean pairs only. No triplets, and no family members reporting the same incident separately.
- **No priority ground truth.** By design, priority comes from the analysts' policy, not from the dataset.
- **Single author.** All labels come from one rules-based author, with no inter-annotator agreement. A model trained on similar templates could pick up phrasing cues, so treat scores as an upper bound on real-world performance.

## Held-out set

`holdout/` is the same generator with `--seed 7331`: the same archetypes and composition, with different people, numbers and slot choices. It was generated after the redactor's first blind run and was never used to tune anything. `python3 -m evals.redaction_check` reports redaction on both sets, and the held-out figure is the one to quote. It tests whether fixes generalize to new names and details. It does not test new phrasing, because the archetypes are shared.

```
python3 generate.py --seed 7331 --out holdout
```
