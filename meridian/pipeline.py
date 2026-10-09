"""Orchestrates one complaint through intake, redaction, the agents and the policy.

    intake ─► redact ─► tripwire ─┐
                                  ├─► [screener | triage | router]  (parallel, redacted text only)
                                  │            │
                                  │      policy (facts ─► priority, per analyst policy)
                                  │            │
                                  └────────► drafter ─► reviewer ─(revise once)─► analyst queue

Deterministic code decides the order, the flags and the queue. Models only
read, extract, route and draft. Every failure fails closed to a person.
"""
from __future__ import annotations

import difflib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, policy, tripwire
from .agents import drafter, reviewer, router, screener, triage
from .audit import AuditLog
from .gate import EgressBlocked, EgressGate
from .intake import CHANNEL_LABEL, CustomerDirectory, normalize
from .llm import LLM, ModelRefused, ModelUnavailable
from .redact import Redactor

PIPELINE_VERSION = "0.3.0"
MANUAL = "manual_triage"
WAITING = "awaiting_analyst"
APPROVED = "approved"


class Store:
    """Case files. `cases/` holds what the agents saw (redacted); `private/` holds originals and the vault."""

    def __init__(self, var: Path):
        self.cases = var / "cases"
        self.private = var / "private"
        self.cases.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(parents=True, exist_ok=True)

    def save(self, case: dict, private: dict | None = None):
        (self.cases / f"{case['id']}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False))
        if private is not None:
            (self.private / f"{case['id']}.json").write_text(json.dumps(private, indent=1, ensure_ascii=False))

    def get(self, case_id: str) -> dict:
        return json.loads((self.cases / f"{case_id}.json").read_text())

    def get_private(self, case_id: str) -> dict:
        return json.loads((self.private / f"{case_id}.json").read_text())

    def all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.cases.glob("*.json"))]


class Pipeline:
    def __init__(self, crm: dict | None, var: Path | None = None, mode: str | None = None, llm: LLM | None = None,
                 cache_dir: Path | None = None):
        self.var = Path(var or config.VAR)
        self.var.mkdir(parents=True, exist_ok=True)
        self.directory = CustomerDirectory(crm)
        self.redactor = Redactor(self.directory.names())
        self.gate = EgressGate(self.directory.customers)
        self.llm = llm or LLM(self.gate, mode=mode, var=self.var, cache_dir=cache_dir)
        self.audit = AuditLog(self.var / "audit.jsonl")
        self.store = Store(self.var)

    # ------------------------------------------------------------------ run
    def process(self, raw: dict) -> dict:
        t0 = time.monotonic()
        cid = raw["id"]
        c = normalize(raw, self.directory)
        label = CHANNEL_LABEL[c.channel]
        self.audit.append(cid, "system:intake", "intake.received",
                          {"channel": c.channel, "customer_matched": c.customer is not None})

        red = self.redactor.redact(c.text, c.known)
        if drafter.CUSTOMER_TOKEN not in red.vault:
            red.vault[drafter.CUSTOMER_TOKEN] = (c.customer or {}).get("name") or c.sender.get("name") or "Customer"
        self.audit.append(cid, "system:redactor", "redaction.done", {"counts": red.counts()})

        trip = tripwire.scan(c.text)
        case = {
            "id": cid, "channel": c.channel, "received_at": c.received_at, "customer_id": (c.customer or {}).get("customer_id"),
            "model": self.llm.model, "pipeline_version": PIPELINE_VERSION,
            "redacted_text": red.text, "redaction_counts": red.counts(),
            "regulatory": {"flagged": False, "categories": [], "sources": {"tripwire": trip, "screener": None},
                           "status": "none", "due_at": None, "flagged_at": None},
            "triage": None, "facts": None, "priority": None, "route": None, "draft": None,
            "injection_suspected": False, "status": WAITING, "failures": [], "usage": {}, "calls": [],
        }
        private = {"original_text": c.text, "sender": c.sender, "vault": red.vault, "raw": raw}
        if trip:
            self._raise_flag(case, "tripwire", [h["category"] for h in trip])
            self._publish_flag(case, c.received_at, round(time.monotonic() - t0, 2), private)

        # Parallel analysis on redacted text only.
        jobs = {"screener": lambda: screener.run(self.llm, cid, red.text, label),
                "triage": lambda: triage.run(self.llm, cid, red.text, label),
                "router": lambda: router.run(self.llm, cid, red.text, label)}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {name: ex.submit(fn) for name, fn in jobs.items()}
        results = {name: self._settle(case, name, f) for name, f in futures.items()}
        # When the flag decision is known. Replayed calls take ~0 s, so use the recorded live latency when larger.
        recorded = [c["latency_s"] for c in case["calls"] if c["agent"] in ("regulatory_screener", "triage_analyst", "router")]
        screen_s = round(max([time.monotonic() - t0] + recorded), 2)

        scr = results["screener"]
        if scr is not None:
            case["regulatory"]["sources"]["screener"] = scr
            case["injection_suspected"] |= scr["injection_suspected"]
            if scr["potential_regulatory"]:
                self._raise_flag(case, "screener", [x["id"] for x in scr["categories"]] or ["unspecified"])
        else:
            # Could not screen: a person must, inside the same window.
            self._raise_flag(case, "screen_failed", ["unscreened"])

        if case["regulatory"]["flagged"] and case["regulatory"]["status"] == "none":
            self._publish_flag(case, c.received_at, screen_s, private)
        elif case["regulatory"]["flagged"]:
            self.store.save(case, private)      # add screener evidence to the already-visible flag

        tri, rte = results["triage"], results["router"]
        if tri is not None:
            case["triage"] = tri
            case["facts"] = triage.flat_facts(tri)
            case["priority"] = policy.evaluate_all(case["facts"])
            case["injection_suspected"] |= tri["injection_suspected"]
        if rte is not None:
            case["route"] = rte
            case["injection_suspected"] |= rte["injection_suspected"]

        case["regulatory"]["lane"] = _lane(case)
        if tri is None or rte is None:
            case["status"] = MANUAL
        else:
            case["status"] = WAITING
            self._draft(case, red.text, label)

        if case["injection_suspected"]:
            self.audit.append(cid, "system:pipeline", "injection.suspected", {"note": "complaint text addresses an AI system"})

        latency = round(time.monotonic() - t0, 2)
        case["latency_s"] = latency
        case["usage"] = _sum_usage(case["calls"])
        self.audit.append(cid, "system:pipeline", "case.queued",
                          {"status": case["status"], "flagged": case["regulatory"]["flagged"],
                           "route": (case["route"] or {}).get("primary"), "latency_s": latency})
        self.store.save(case, private)
        return case

    def _publish_flag(self, case: dict, received_at: str, after_s: float, private: dict):
        """Put a flagged complaint in front of Compliance now, before drafting starts."""
        reg = case["regulatory"]
        received = datetime.fromisoformat(received_at)
        reg["flagged_at"] = (received + timedelta(seconds=after_s)).isoformat(timespec="seconds")
        reg["seconds_to_flag"] = after_s
        reg["due_at"] = (received + timedelta(hours=config.taxonomy()["sla_hours"])).isoformat(timespec="seconds")
        reg["status"] = "open"
        case["status"] = "drafting"
        self.store.save(case, private)
        self.audit.append(case["id"], "system:pipeline", "compliance.queued", {"seconds_after_receipt": after_s})

    def _settle(self, case: dict, name: str, future):
        try:
            out, rec = future.result()
            case["calls"].append(asdict(rec))
            return out
        except EgressBlocked as e:
            case["failures"].append({"stage": name, "reason": "egress_blocked", "kinds": sorted({f['kind'] for f in e.findings})})
            self.audit.append(case["id"], "system:egress_gate", "egress.blocked",
                              {"stage": name, "kinds": sorted({f["kind"] for f in e.findings})})
        except ModelRefused as e:
            case["failures"].append({"stage": name, "reason": "model_refused", "detail": str(e)})
            self.audit.append(case["id"], f"agent:{name}", "model.refused", {"category": str(e)})
        except ModelUnavailable as e:
            case["failures"].append({"stage": name, "reason": "model_unavailable", "detail": str(e)[:200]})
            self.audit.append(case["id"], f"agent:{name}", "model.unavailable", {"detail": str(e)[:200]})
        return None

    def _raise_flag(self, case: dict, source: str, categories: list[str]):
        reg = case["regulatory"]
        new = [x for x in categories if x not in reg["categories"]]
        reg["flagged"] = True                     # one-way: nothing in the pipeline sets it back
        reg["categories"].extend(new)
        self.audit.append(case["id"], f"system:{source}" if source != "screener" else "agent:regulatory_screener",
                          "regulatory.flag_raised", {"source": source, "categories": categories})

    def _draft(self, case: dict, redacted: str, label: str):
        from concurrent.futures import Future
        cid = case["id"]
        mode = "ACKNOWLEDGE ONLY" if case["regulatory"]["flagged"] else "STANDARD"
        route_name = next(d["name"] for d in config.departments() if d["id"] == case["route"]["primary"])

        def attempt(stage, fn):
            f: Future = Future()
            try:
                f.set_result(fn())
            except Exception as e:  # noqa: BLE001 - routed through _settle's typed handling
                f.set_exception(e)
            return self._settle(case, stage, f)

        d = attempt("drafter", lambda: drafter.run(self.llm, cid, redacted, label, mode=mode, triage=case["triage"], route_name=route_name))
        revisions = 0
        rv = det = None
        while d is not None:
            det = reviewer.deterministic_checks(d)
            rv = attempt("draft_reviewer", lambda: reviewer.run(self.llm, cid, redacted, label, mode=mode, draft=d, route_name=route_name))
            problems = (rv or {}).get("revision_instructions", []) if (rv and rv["verdict"] == "revise") else []
            problems += [f"Remove: {k.replace('_', ' ')}" for k in det["hard_failures"]]
            if not problems or revisions >= 1:
                break
            revisions += 1
            self.audit.append(cid, "agent:draft_reviewer", "draft.returned", {"points": len(problems)})
            prev = d
            d = attempt("drafter", lambda: drafter.run(self.llm, cid, redacted, label, mode=mode, triage=case["triage"],
                                                       route_name=route_name, feedback=problems, previous=prev)) or prev
            if d is prev:
                break
        if d is None:
            case["status"] = MANUAL
            return
        ready = rv is not None and rv["verdict"] == "pass" and not det["hard_failures"]
        case["draft"] = {"mode": mode, "subject": d["subject"], "body": d["body"], "notes_for_analyst": d["notes_for_analyst"],
                         "review": rv, "deterministic": det, "revisions": revisions,
                         "state": "ready_for_analyst" if ready else "needs_attention"}
        self.audit.append(cid, "agent:drafter", "draft.prepared",
                          {"mode": mode, "revisions": revisions, "state": case["draft"]["state"]})

    # --------------------------------------------------------- human actions
    def approve(self, case_id: str, reviewer_name: str, final_subject: str, final_body: str) -> dict:
        reviewer_name = (reviewer_name or "").strip()
        if not reviewer_name:
            raise PermissionError("approval needs a named reviewer")
        case = self.store.get(case_id)
        if case["status"] == APPROVED:
            raise PermissionError("this reply was already approved; the record cannot be overwritten")
        reg = case["regulatory"]
        if reg["flagged"] and reg["status"] == "open":
            raise PermissionError("Compliance must acknowledge this regulatory flag before a reply can be approved")
        if not final_body.strip():
            raise ValueError("empty reply")
        priv = self.store.get_private(case_id)
        drafted = self.rehydrate(case_id, (case.get("draft") or {}).get("body", ""), priv)
        ratio = difflib.SequenceMatcher(None, drafted, final_body).ratio() if drafted else 0.0
        case["status"] = APPROVED
        unchanged = drafted.strip() == final_body.strip()
        case["approval"] = {"by": reviewer_name, "at": _now(), "subject": final_subject, "body": final_body,
                            "unchanged": unchanged, "similarity_to_draft": round(ratio, 3)}
        self.store.save(case)
        self.audit.append(case_id, f"analyst:{reviewer_name}", "reply.approved",
                          {"unchanged": unchanged, "similarity_to_draft": round(ratio, 3), "subject": final_subject, "body": final_body,
                           "note": "approved for the bank's existing outbound process; this system cannot send"})
        return case

    def compliance_action(self, case_id: str, officer: str, action: str, reason: str = "") -> dict:
        officer = (officer or "").strip()
        if not officer:
            raise PermissionError("Compliance actions need a named officer")
        case = self.store.get(case_id)
        reg = case["regulatory"]
        if not reg["flagged"]:
            raise ValueError("case is not flagged")
        if action == "acknowledge":
            reg["status"] = "acknowledged"
        elif action == "clear":
            if not reason.strip():
                raise ValueError("clearing a flag needs a reason")
            reg["status"] = "cleared"
            reg["cleared_reason"] = reason
        else:
            raise ValueError(action)
        reg["actioned_by"], reg["actioned_at"] = officer, _now()
        self.store.save(case)
        self.audit.append(case_id, f"compliance:{officer}", f"regulatory.{action}", {"reason": reason})
        return case

    def reroute(self, case_id: str, analyst: str, department: str, reason: str = "") -> dict:
        if department not in config.dept_ids():
            raise ValueError(department)
        case = self.store.get(case_id)
        before = (case.get("route") or {}).get("primary")
        case.setdefault("route", {})["primary"] = department
        case["route"]["overridden_by"] = analyst
        self.store.save(case)
        self.audit.append(case_id, f"analyst:{analyst}", "route.changed", {"from": before, "to": department, "reason": reason})
        return case

    def rehydrate(self, case_id: str, text: str, private: dict | None = None) -> str:
        from .redact import TOKEN_RE
        vault = (private or self.store.get_private(case_id))["vault"]
        return TOKEN_RE.sub(lambda m: vault.get(m.group(0), m.group(0)), text)


def _lane(case: dict) -> str | None:
    """Which Compliance lane a flag belongs in. Every flag reaches Compliance; the lane only says how.

    review_48h      the model screener flagged it, or the screen could not run
    tripwire_check  only the Compliance tripwire fired and the model screener read it as not regulatory
    """
    reg = case["regulatory"]
    if not reg["flagged"]:
        return None
    scr = reg["sources"]["screener"]
    if scr is None or scr.get("potential_regulatory"):
        return "review_48h"
    return "tripwire_check"


def _sum_usage(calls: list[dict]) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd")
    out = {k: round(sum(c.get(k, 0) for c in calls), 6) for k in keys}
    out["calls"] = len(calls)
    out["cached_calls"] = sum(1 for c in calls if c.get("cached"))
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
