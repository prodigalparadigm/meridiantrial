"""Deterministic regulatory tripwire. Compliance owns the terms (config/tripwire.json).

Runs on the original complaint text, locally. A hit raises a flag by itself;
the model screener can add flags but nothing can remove one except a named
Compliance reviewer.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import config


@lru_cache
def _compiled() -> dict[str, list[tuple[str, re.Pattern]]]:
    return {cat: [(t, re.compile(t, re.I)) for t in terms] for cat, terms in config.tripwire_terms().items()}


def scan(text: str) -> list[dict]:
    hits = []
    for cat, pats in _compiled().items():
        for term, rx in pats:
            m = rx.search(text)
            if m:
                hits.append({"category": cat, "term": term, "matched": m.group(0)})
                break   # one hit per category is enough to flag it
    return hits
