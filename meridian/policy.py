"""Turns extracted facts into a priority, using a policy file the analysts own.

The model never sees these rules and never outputs a priority. That keeps the
definition of "serious" a business decision that can be read, argued over and
changed without touching a prompt.
"""
from __future__ import annotations

from . import config

ORDER = {"P1": 1, "P2": 2, "P3": 3}


def _matches(cond: dict, facts: dict) -> bool:
    for key, want in cond.items():
        have = facts.get(key)
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


def evaluate(policy_id: str, facts: dict) -> dict:
    """Returns {"priority": "P1", "because": [...conditions that fired...]}."""
    pol = config.policies()[policy_id]
    if facts.get("is_complaint") is False:
        # Compliments and questions never outrank complaints, whatever else is true.
        return {"priority": "P3", "because": [{"is_complaint": False}]}
    if "max_of" in pol:
        results = [evaluate(p, facts) for p in pol["max_of"]]
        best = min(results, key=lambda r: ORDER[r["priority"]])
        return {"priority": best["priority"], "because": best["because"],
                "components": {p: r["priority"] for p, r in zip(pol["max_of"], results)}}
    for prio in ("P1", "P2"):
        for cond in pol["rules"].get(prio, []):
            if _matches(cond, facts):
                return {"priority": prio, "because": [cond]}
    return {"priority": pol.get("default", "P3"), "because": []}


def evaluate_all(facts: dict) -> dict[str, dict]:
    return {pid: evaluate(pid, facts) for pid in config.policies()}
