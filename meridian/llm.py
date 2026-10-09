"""The only module that talks to a model. Every call passes the egress gate first.

Modes
  live    call Claude; cache the response (keyed by a hash of the redacted payload)
  auto    use the cache when it has the exact payload, otherwise call Claude
  replay  cache only; a miss fails closed (the complaint goes to manual triage)

Replay is what makes the Friday demo independent of the network: the recorded
responses are keyed to redacted payloads only, so the cache holds no PII.

Refusals are not retried on a different model. A bank's model-risk process
validates a specific model; a silent swap would put an unvalidated model in
the loop. A refusal goes to a person instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .gate import EgressGate

# $ per million tokens, claude-opus-5-5 first-party API.
PRICE = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}


class ModelRefused(Exception):
    pass


class ModelUnavailable(Exception):
    pass


@dataclass
class CallRecord:
    agent: str
    case_id: str
    model: str
    cached: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    request_id: str = ""


class LLM:
    def __init__(self, gate: EgressGate, mode: str | None = None, var: Path | None = None,
                 model: str = config.MODEL, client=None, cache_dir: Path | None = None):
        self.gate = gate
        self.mode = mode or os.environ.get("MERIDIAN_LLM", "auto")
        var = Path(var or config.VAR)
        self.cache_dir = Path(cache_dir or os.environ.get("MERIDIAN_CACHE", var / "llm_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.egress_log = var / "egress.jsonl"
        self.model = model
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            import anthropic   # imported here so replay mode and tests need no key
            self._client = anthropic.Anthropic(max_retries=4, timeout=180.0)
        return self._client

    def call(self, *, case_id: str, agent: str, system: str, user: str, schema: dict,
             effort: str = "low", max_tokens: int = 8000) -> tuple[dict, CallRecord]:
        # 1. Gate. Raises EgressBlocked; nothing below runs if it does.
        self.gate.enforce(system, user)

        key = hashlib.sha256(json.dumps({"model": self.model, "agent": agent, "system": system, "user": user,
                                         "schema": schema, "effort": effort}, sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if self.mode in ("auto", "replay") and cache_file.exists():
            hit = json.loads(cache_file.read_text())
            # Carry the recorded usage so cost and latency reports reflect the original live call.
            rec = CallRecord(**{**hit.get("usage", {}), "agent": agent, "case_id": case_id, "model": self.model, "cached": True})
            self._log(case_id, agent, key, system, user, sent=False, rec=rec)
            return hit["output"], rec
        if self.mode == "replay":
            raise ModelUnavailable(f"replay cache miss for {agent}")

        # 2. Send. Log the exact outbound payload first, so the record exists even if the call fails.
        rec = CallRecord(agent=agent, case_id=case_id, model=self.model, cached=False)
        self._log(case_id, agent, key, system, user, sent=True, rec=None)
        t0 = time.monotonic()
        import anthropic
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ModelUnavailable(f"API {e.status_code}") from e
            raise
        rec.latency_s = round(time.monotonic() - t0, 2)
        rec.request_id = getattr(resp, "_request_id", "") or ""
        u = resp.usage
        rec.input_tokens = u.input_tokens or 0
        rec.output_tokens = u.output_tokens or 0
        rec.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
        rec.cache_write_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0
        rec.cost_usd = round((rec.input_tokens * PRICE["input"] + rec.output_tokens * PRICE["output"]
                              + rec.cache_write_tokens * PRICE["cache_write"] + rec.cache_read_tokens * PRICE["cache_read"]) / 1e6, 6)

        if resp.stop_reason == "refusal":
            raise ModelRefused(getattr(resp.stop_details, "category", None) or "refusal")
        if resp.stop_reason == "max_tokens":
            raise ModelUnavailable("max_tokens reached before a complete answer")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ModelUnavailable("no text block in response")
        output = json.loads(text)
        cache_file.write_text(json.dumps({"agent": agent, "model": self.model, "output": output,
                                          "recorded_at": _now(), "usage": asdict(rec)}, indent=1))
        return output, rec

    def _log(self, case_id, agent, key, system, user, sent: bool, rec):
        entry = {"at": _now(), "case_id": case_id, "agent": agent, "model": self.model, "payload_sha256": key,
                 "sent_to_model": sent, "system": system, "user": user}
        with self._lock, self.egress_log.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
