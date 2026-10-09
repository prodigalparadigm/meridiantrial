# Scoping — what Meridian Harbor asked for, and what it needs

## The ask

> "We want AI to handle complaints." — Head of Retail Operations

About 1,400 complaints a month arrive by email, web form and call-center notes. Three analysts read every one, decide how serious it is, work out which department owns it, and draft a response. On average they are six days behind.

## What discovery says the problem actually is

**1. A compliance-timing failure is hidden inside the backlog.** Complaints that touch regulatory issues (disputed transactions, fee practices, unfair treatment) must reach Compliance within 48 hours. Nobody can know a complaint is regulatory until someone reads it, and the queue is six days deep, so most regulatory complaints miss the window. The backlog is what people see. The late flags carry the regulatory risk.

**2. The bottleneck is reading and sorting, not judgment.** Each complaint gets four jobs: read it, judge it, route it, draft a reply. Reading, sorting and first drafts are mechanical. Judgment and sign-off are not, and the bank has said both stay with people.

**3. Nobody has defined "serious," and no model can define it for the bank.** The two senior analysts disagree about it in front of us. A model asked to decide would quietly pick a definition that nobody chose. That is a business decision, and the analysts own it.

**4. One person can veto the project, and she has before.** Compliance stopped the last vendor pilot. Any design that asks her to trust model-written text, or to trust that customer data stays inside the bank, will fail with her for the same reasons.

## What we are building, and what we are not

**Building:** an intake assistant that reads every complaint as it arrives and then:
- flags possible regulatory issues to Compliance right away, not after the queue;
- routes the complaint to the department that owns it;
- pulls out the facts that drive priority, quoting the complaint for each;
- prepares a draft reply for an analyst to edit and sign.

**Not building:** anything that sends text to a customer, anything that replaces an analyst, or a model-decided "serious."

## Requirements → design → evidence

| What we heard | Design decision | How we prove it |
|---|---|---|
| Compliance will not accept customer-facing text without human sign-off | The system cannot send anything. There is no outbound channel in the code. Approval needs a named analyst, and the audit log keeps the draft and the approved text side by side. | A structural test fails the build if any sending capability appears. The audit trail is hash-chained so tampering shows. |
| Regulatory complaints must reach Compliance within 48 hours, and today they arrive late | The regulatory screen runs at intake, before any queue. Two independent checks, a keyword tripwire Compliance controls and a model screener, can each raise a flag. Neither can lower one. Only Compliance can clear a flag. | Recall on labeled test data, broken out by category and by each check alone vs. combined. Time from receipt to flag. |
| Complaint text contains PII, and none of it may leave the bank's environment un-redacted | Redaction runs on bank hardware before any model call. A separate egress gate re-scans every outbound payload and fails closed: if it finds residual PII, nothing is sent and the complaint goes to manual triage. Every outbound payload is logged. | A leak scan checks every logged outbound payload against every PII value planted in the test data. |
| The analysts stay. This is about the backlog, not headcount | The product is an analyst workbench: a pre-sorted queue, a summary, a route and a draft per complaint. It is not an autopilot. | In pilot: analyst minutes per complaint and backlog age. |
| Nobody can say what "serious" means | The model extracts facts (money at stake, customer vulnerability, ongoing harm, deadline, escalation, repeat contact) with a quote for each. A policy file the analysts own turns facts into priority. Both analysts' views run side by side, and the tool lists exactly where they disagree. | Fact-extraction accuracy per dimension, plus a disagreement report the analysts can settle in one meeting. |
| Compliance stopped the last pilot | Compliance owns the regulatory taxonomy, the tripwire terms and flag clearance, all in plain files she can read. The model is pinned and recorded on every case. If the model refuses, the complaint goes to a person, never to a different model. | Decisions she can see in the case file and in the audit log. |

## Back-of-envelope (assumptions to verify in week one, not claims)

- 1,400 a month is about 65 per business day, or about 22 per analyst per day.
- Six days behind means roughly 390 complaints waiting.
- The queue is growing, so capacity sits just under arrivals. If pre-sorting and first drafts cut handling time by 30%, capacity rises from about 65 to about 93 a day. That surplus of about 28 a day clears 390 in about three weeks.
- The 30% is a placeholder. The pilot measures it by timing a sample of complaints handled with and without the assistant.
- The regulatory benefit doesn't depend on the backlog. Flags reach Compliance within minutes of a complaint arriving, from day one.

## Success measures proposed for the pilot

1. **Regulatory recall:** every regulatory complaint in the test set is flagged. This is a floor. Precision is reported too, so Compliance knows the queue load before agreeing.
2. **Time to flag:** median time from receipt to Compliance flag under one hour.
3. **Zero leaks:** no un-redacted PII in the egress log, ever. One leak stops the pilot.
4. **Backlog age:** from six days to under two business days. The target date is set after week-one timing.
5. **Draft usefulness:** share of drafts approved with light edits, measured by the edit distance the audit log already records.

## Questions to settle on Friday

**For Compliance**
1. The taxonomy has three categories from the brief and five proposed ones (credit reporting, collections conduct, privacy incidents, servicing errors, suspected exploitation). Keep or strike each.
2. Does "our environment" include Meridian's own cloud tenancy? If so, production can call Claude through Amazon Bedrock or Google Vertex in Meridian's account, so no complaint text crosses to an outside API. Redaction then stays on as a second layer.
3. Which reply types can use a pre-approved template (simple acknowledgements), and which need her review?
4. Who besides her can clear a regulatory flag?
5. Replies to Spanish-speaking customers. Drafts are written in English so the approving analyst can read every word they sign. Should the bank reply in Spanish? If so, who approves Spanish text? The text that goes out has to be the text that was approved.

**For Operations**
6. Thirty minutes with both senior analysts. Each ranks the same 20 complaints, and we encode the result as the bank's priority policy. The prototype already lists the complaints where their two views diverge.
7. Which system sends approved replies today? The prototype stops at "approved."
8. Pilot shape: two weeks in shadow mode, where the assistant runs alongside the analysts but nobody acts on it, then compare.

## Deliberately out of scope for the prototype

- Connections to the real mailbox, CRM or case-management system. Synthetic data stands in.
- Single sign-on and role-based access. The UI asks for a reviewer name and does not authenticate.
- A production named-entity model for names. The prototype uses the customer directory plus rules, and measures the leftover risk instead of hiding it.
- Replies in Spanish. Spanish complaints are triaged, flagged and routed, and the draft is in English for the analyst to adapt.

## Risks we are designing against

- **Rubber-stamping.** Analysts may approve drafts without reading them. Mitigation: reviewer-agent notes appear on the draft, and the audit log records edit distance and time-to-approve. Spot checks look for outliers.
- **Over-flagging.** Too many flags would flood Compliance. Mitigation: precision is reported, and Compliance can tune the tripwire.
- **Name redaction is the weakest layer.** Mitigation: it is measured, the gate re-checks against the full customer directory, and production adds a local named-entity model or runs in the bank's tenancy.
- **Model drift.** Mitigation: the model version is pinned and written on every case. The evaluation suite re-runs on any change before it ships, which fits standard model-risk-management practice.
