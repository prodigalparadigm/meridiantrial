"""Loads the stakeholder-owned files in config/. Prompts read from here; nothing is duplicated in code."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
VAR = Path(os.environ.get("MERIDIAN_VAR", ROOT / "var"))      # local runtime state: cases, vault, audit, egress log

MODEL = os.environ.get("MERIDIAN_MODEL", "claude-opus-5-5")   # pinned; recorded on every case
RESPONSE_WINDOW = "within 10 business days"                    # the only timeframe a draft may promise
BANK_NAME = "Meridian Harbor Bank"


def _load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text())


@lru_cache
def departments() -> list[dict]:
    return _load("departments.json")["departments"]


@lru_cache
def taxonomy() -> dict:
    return _load("compliance_taxonomy.json")


@lru_cache
def rubric() -> list[dict]:
    return _load("severity_rubric.json")["dimensions"]


@lru_cache
def tripwire_terms() -> dict[str, list[str]]:
    return _load("tripwire.json")["terms"]


@lru_cache
def policies() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((CONFIG / "policies").glob("*.json"))}


def dept_ids() -> list[str]:
    return [d["id"] for d in departments()]


def category_ids() -> list[str]:
    return [c["id"] for c in taxonomy()["categories"]]
