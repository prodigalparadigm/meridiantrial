"""Append-only, hash-chained audit log.

Each entry carries the hash of the previous one, so any edit, deletion or
reordering after the fact breaks `verify()`. Entries record actors, decisions
and reasons. PII values never go in the detail; customer-facing text that a
named analyst approved does, because that is the record of what was sent.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_hash, self._seq = self._tail()

    def _tail(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS, 0
        last, seq = GENESIS, 0
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    last, seq = e["hash"], e["seq"]
        return last, seq

    @staticmethod
    def _digest(entry: dict) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def append(self, case_id: str | None, actor: str, event: str, detail: dict | None = None, at: str | None = None) -> dict:
        with self._lock:
            entry = {"seq": self._seq + 1, "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "case_id": case_id, "actor": actor, "event": event, "detail": detail or {},
                     "prev_hash": self._last_hash}
            entry["hash"] = self._digest(entry)
            with self.path.open("a") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._last_hash, self._seq = entry["hash"], entry["seq"]
            return entry

    def entries(self, case_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
        return [e for e in out if case_id is None or e["case_id"] == case_id]

    def verify(self) -> dict:
        prev, n = GENESIS, 0
        for e in self.entries():
            n += 1
            if e["prev_hash"] != prev or self._digest(e) != e["hash"]:
                return {"ok": False, "entries": n, "broken_at_seq": e["seq"]}
            prev = e["hash"]
        return {"ok": True, "entries": n, "head": prev}
