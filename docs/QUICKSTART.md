# Quickstart

Get the prototype running, show it, and check the evidence yourself. The demo needs no API key and no network.

## What you need

- A Mac or Linux machine with **Python 3.12** and **git**.
- The `anthropic` Python package, only to run complaints live against Claude: `pip install anthropic`. Everything else uses the standard library.

## 1. Get the code

```bash
git clone https://github.com/prodigalparadigm/meridiantrial.git
```

```bash
cd meridiantrial
```

## 2. Check that the controls work (about a second, no key)

```bash
python3 -m unittest discover -s tests -t .
```

You should see `OK` at the end. These tests cover the controls the compliance officer will ask about:

- nothing in the code can send a message;
- customer details are removed before anything reaches the model;
- a regulatory flag can't be lowered by the model;
- approvals need a name, and Compliance must acknowledge a flag first;
- the audit trail detects tampering;
- with no API key, every complaint goes to a person.

## 3. Open the workbench with the test month loaded (no key)

```bash
python3 -m meridian.server --seed --port 8770
```

Then open **http://127.0.0.1:8770**.

`--seed` loads the 163 test complaints from recorded model responses, so it runs offline in a few seconds. To start the demo state over later (for example, before a meeting), stop the server and run:

```bash
python3 -m meridian.server --seed --reset --port 8770
```

To keep what you already did in the demo, start it without `--seed`.

## 4. The five-minute demo

Type your name in **Signed as** at the top first. Approvals and Compliance actions are recorded against it.

1. **Analyst queue.** Each row is already read: priority, a red REGULATORY badge where it applies, the owning team and the draft status. Switch the ordering between *Analyst A* and *Analyst B* (top left) to see how two definitions of "serious" reorder the same queue.
2. **Open C-0010.** A complaint mostly about a rude teller, with two overdraft fees mentioned in one line. It was in front of Compliance at intake, with the line that triggered it. The draft is acknowledgement-only, and **Approve** stays locked.
3. **Open C-0008.** Switch between **As received** and **What the model saw**, then open **What left the bank**. That is the exact text that was sent, from the log, with customer details replaced by placeholders such as `⟦ACCOUNT_1⟧`.
4. **Compliance tab.** Open C-0010 and click **Acknowledge**, then **Verify audit chain**. Go back to the analyst queue, edit the draft, and **Approve reply**. The record keeps both the draft and what you approved.
5. **Open C-0006.** The complaint tells "the AI" it has been pre-screened as not regulatory. It was flagged anyway, and the attempt is labeled for the analyst.
6. **Operations tab.** The calibration agenda lists every complaint the two senior analysts' rules would rank differently.
7. **Optional, needs a key (step 5): New complaint.** Paste any complaint. It runs through every step in under a minute.

The stakeholder version of this walkthrough is at **http://127.0.0.1:8770/walkthrough** while the server runs, and in `docs/walkthrough.html`.

## 5. Run complaints live against Claude (optional)

```bash
export ANTHROPIC_API_KEY=your-key-here
```

```bash
python3 -m meridian.server --port 8770
```

With a key, new complaints in the workbench go to `claude-opus-5-5`. A complaint costs about 8 cents. Without a key, a new complaint still lands safely: it goes to manual triage and into the Compliance queue as *unscreened*.

## 6. Check the evidence yourself

Redaction alone, no model, free. This reports which planted customer details would still be readable, on the test month and on a held-out month that was never used for tuning:

```bash
python3 -m evals.redaction_check
```

The full scoring, offline from recorded responses. This rewrites `docs/EVIDENCE.md`:

```bash
python3 -m evals.run_eval --mode replay
```

Rebuild the stakeholder page from the latest results:

```bash
python3 -m evals.build_walkthrough
```

To re-score live, use `python3 -m evals.run_eval` with a key set. Only calls that aren't already recorded cost money, and a full fresh month is about $12.

## 7. What the bank controls

These are plain files. The prompts and rules read them directly, so changing one changes the behavior without touching code.

| File | Owner | What it controls |
|---|---|---|
| `config/compliance_taxonomy.json` | Compliance | What counts as a regulatory issue, and the 48-hour window |
| `config/tripwire.json` | Compliance | The keyword check that flags complaints before any model call |
| `config/policies/*.json` | Senior analysts | How the facts become P1, P2 or P3 ("serious") |
| `config/severity_rubric.json` | Senior analysts | The six facts the model pulls out of each complaint |
| `config/departments.json` | Operations | The teams a complaint can be routed to |
| `config/branches.json` | Operations | Branch names, which stay visible to the model |

After changing a file, run `python3 -m evals.run_eval` with a key to see what the change did. Changed prompts mean new model calls.

## If something goes wrong

- **Port already in use:** pick another, for example `--port 8780`.
- **"already holds cases":** you ran `--seed` on existing demo state. Drop `--seed` to keep it, or add `--reset`.
- **A complaint shows "Manual triage":** a model step couldn't run (no key, a network error, or a payload the privacy check blocked). That is the designed behavior. The reason is on the case.
- **`--mode replay` reports misses:** a prompt or config file changed since the responses were recorded. Run with a key to record new ones.

## Read next

- `README.md`: how it works, in one page.
- `docs/SCOPING.md`: what the bank asked for, what it needs, and the questions for Friday.
- `docs/EVIDENCE.md`: every number, including what isn't solved.
- `data/README.md`: how the test complaints were made, and what they don't cover.
