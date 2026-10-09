"""Local workbench server. Binds to 127.0.0.1 only; standard library only.

    python3 -m meridian.server            # serve var/ (seed it first with --seed)
    python3 -m meridian.server --seed     # process the synthetic month into var/ (replay: offline, no key needed)

This is an inbound-only UI server for people inside the bank. It has no
route that sends anything to a customer.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, policy
from .pipeline import Pipeline

UI = config.ROOT / "ui"
DATA = config.ROOT / "data" / "synthetic"
CACHE = config.ROOT / "evals" / "llm_cache"
STATE: dict = {}
_new_id_lock = threading.Lock()


def _load_crm() -> dict:
    p = DATA / "crm.json"
    return json.loads(p.read_text()) if p.exists() else {"customers": []}


def _rehydrate(obj, vault: dict):
    """Fill tokens back in for people inside the bank. Model outputs stay tokenized on disk."""
    from .redact import TOKEN_RE
    if isinstance(obj, str):
        return TOKEN_RE.sub(lambda m: vault.get(m.group(0), m.group(0)), obj)
    if isinstance(obj, list):
        return [_rehydrate(x, vault) for x in obj]
    if isinstance(obj, dict):
        return {k: _rehydrate(v, vault) for k, v in obj.items()}
    return obj


def _for_people(c: dict, vault: dict) -> dict:
    out = dict(c)
    for k in ("triage", "route", "draft"):
        out[k] = _rehydrate(c.get(k), vault)
    out["regulatory"] = {**c["regulatory"], "sources": _rehydrate(c["regulatory"]["sources"], vault)}
    return out


def _row(c: dict, pol: str, vault: dict | None = None) -> dict:
    tri = _rehydrate(c.get("triage") or {}, vault or {})
    pri = (c.get("priority") or {}).get(pol, {}).get("priority")
    return {"id": c["id"], "channel": c["channel"], "received_at": c["received_at"], "summary": tri.get("summary", ""),
            "priority": pri or "—", "route": (c.get("route") or {}).get("primary"), "status": c["status"],
            "flagged": c["regulatory"]["flagged"], "reg_status": c["regulatory"]["status"], "lane": c["regulatory"].get("lane"),
            "categories": c["regulatory"]["categories"], "due_at": c["regulatory"]["due_at"],
            "draft_state": (c.get("draft") or {}).get("state"), "injection": c.get("injection_suspected", False),
            "language": tri.get("language", ""), "failures": c.get("failures", [])}


def _stats(cases: list[dict]) -> dict:
    flagged = [c for c in cases if c["regulatory"]["flagged"]]
    secs = sorted(c["regulatory"].get("seconds_to_flag") or 0 for c in flagged if c["regulatory"].get("seconds_to_flag"))
    model_secs = sorted(c["regulatory"]["seconds_to_flag"] for c in flagged
                        if not c["regulatory"]["sources"]["tripwire"] and c["regulatory"].get("seconds_to_flag"))
    by_dept: dict = {}
    for c in cases:
        d = (c.get("route") or {}).get("primary") or "manual"
        by_dept[d] = by_dept.get(d, 0) + 1
    pri = {p: {"P1": 0, "P2": 0, "P3": 0} for p in config.policies()}
    disagree = []
    for c in cases:
        for p, r in (c.get("priority") or {}).items():
            pri[p][r["priority"]] += 1
        pr = c.get("priority") or {}
        if pr and pr["analyst_a"]["priority"] != pr["analyst_b"]["priority"]:
            disagree.append({"id": c["id"], "summary": (c.get("triage") or {}).get("summary", ""),
                             "A": pr["analyst_a"]["priority"], "B": pr["analyst_b"]["priority"], "facts": c.get("facts")})
    drafts = [c["draft"] for c in cases if c.get("draft")]
    costs = [c["usage"].get("cost_usd", 0) for c in cases if c.get("usage")]
    return {
        "total": len(cases),
        "statuses": {s: sum(c["status"] == s for c in cases) for s in ("awaiting_analyst", "manual_triage", "approved")},
        "flagged": len(flagged),
        "flags_open": sum(c["regulatory"]["status"] == "open" for c in flagged),
        "median_seconds_to_flag": secs[len(secs) // 2] if secs else None,
        "median_seconds_to_flag_model": model_secs[len(model_secs) // 2] if model_secs else None,
        "max_seconds_to_flag": secs[-1] if secs else None,
        "by_department": dict(sorted(by_dept.items(), key=lambda kv: -kv[1])),
        "priority": pri, "disagreements": disagree,
        "drafts": {"total": len(drafts), "ready": sum(d["state"] == "ready_for_analyst" for d in drafts)},
        "cost_per_complaint": round(sum(costs) / len(costs), 4) if costs else None,
        "injection": sum(bool(c.get("injection_suspected")) for c in cases),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MeridianWorkbench/0.3"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ---------------------------------------------------------------- utils
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, path: Path):
        if not path.exists() or UI not in path.resolve().parents and path.resolve() != UI:
            self.send_error(404)
            return
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        pipe: Pipeline = STATE["pipe"]
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._static(UI / "index.html")
        if u.path.startswith("/ui/"):
            return self._static(UI / u.path[4:])
        if u.path == "/walkthrough":   # the stakeholder page, offline copy
            page = config.ROOT / "docs" / "walkthrough.html"
            if not page.exists():
                return self.send_error(404, "run python3 -m evals.build_walkthrough")
            body = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
                    'content="width=device-width, initial-scale=1"></head><body style="margin:0">' + page.read_text() + "</body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/api/config":
            return self._json({"departments": config.departments(), "taxonomy": config.taxonomy(),
                               "policies": {k: v["name"] for k, v in config.policies().items()},
                               "rubric": config.rubric(), "model": pipe.llm.model, "llm_mode": pipe.llm.mode})
        if u.path == "/api/cases":
            pol = q.get("policy", ["interim"])[0]
            rows = []
            for c in pipe.store.all():
                try:
                    vault = pipe.store.get_private(c["id"])["vault"]
                except FileNotFoundError:
                    vault = {}
                rows.append(_row(c, pol, vault))
            return self._json(rows)
        m = re.fullmatch(r"/api/cases/([\w\-]+)", u.path)
        if m:
            cid = m.group(1)
            try:
                c = pipe.store.get(cid)
                priv = pipe.store.get_private(cid)
            except FileNotFoundError:
                return self._json({"error": "not found"}, 404)
            draft = c.get("draft") or {}
            return self._json({
                "case": {**_for_people(c, priv["vault"]), "redacted_text": c["redacted_text"]},
                "original_text": priv["original_text"],     # local display only
                "sender": priv["sender"],
                "draft_rehydrated": {"subject": pipe.rehydrate(cid, draft.get("subject", ""), priv),
                                     "body": pipe.rehydrate(cid, draft.get("body", ""), priv)} if draft else None,
                "audit": pipe.audit.entries(cid),
            })
        m = re.fullmatch(r"/api/egress/([\w\-]+)", u.path)
        if m:
            f = pipe.var / "egress.jsonl"
            rows = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
            rows = [{k: r[k] for k in ("at", "agent", "model", "sent_to_model", "payload_sha256", "user")}
                    for r in rows if r["case_id"] == m.group(1) and "user" in r]
            return self._json(rows)
        if u.path == "/api/stats":
            return self._json(_stats(pipe.store.all()))
        if u.path == "/api/audit/verify":
            return self._json(pipe.audit.verify())
        self.send_error(404)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        pipe: Pipeline = STATE["pipe"]
        u = urlparse(self.path)
        try:
            b = self._body()
            m = re.fullmatch(r"/api/cases/([\w\-]+)/(approve|compliance|reroute)", u.path)
            if m:
                cid, action = m.groups()
                if action == "approve":
                    c = pipe.approve(cid, b.get("reviewer", ""), b.get("subject", ""), b.get("body", ""))
                elif action == "compliance":
                    c = pipe.compliance_action(cid, b.get("officer", ""), b.get("action", ""), b.get("reason", ""))
                else:
                    c = pipe.reroute(cid, b.get("analyst", ""), b.get("department", ""), b.get("reason", ""))
                return self._json({"ok": True, "case": c})
            if u.path == "/api/intake":
                with _new_id_lock:
                    n = len(list(pipe.store.cases.glob("N-*.json"))) + 1
                    cid = f"N-{n:04d}"
                now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                ch = b.get("channel", "email")
                text = b.get("text", "")
                if ch == "email":
                    payload = {"from": f"{b.get('name', '')} <{b.get('email', '')}>", "subject": b.get("subject", ""), "body": text}
                elif ch == "web_form":
                    payload = {"name": b.get("name", ""), "email": b.get("email", ""), "phone": b.get("phone", ""),
                               "account_last4": "", "topic_selected": b.get("topic", "Other"), "message": text}
                else:
                    payload = {"agent_id": "CSR-DEMO", "caller_name": b.get("name", ""), "callback": b.get("phone", ""), "note": text}
                c = pipe.process({"id": cid, "channel": ch, "received_at": now, "payload": payload})
                return self._json({"ok": True, "case": c})
            self.send_error(404)
        except (PermissionError, ValueError) as e:
            self._json({"ok": False, "error": str(e)}, 400)


def seed(pipe: Pipeline):
    complaints = [json.loads(l) for l in (DATA / "complaints.jsonl").read_text().splitlines() if l.strip()]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(pipe.process, complaints))
    print(f"seeded {len(complaints)} complaints into {pipe.var}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--var", default=str(config.VAR))
    ap.add_argument("--seed", action="store_true", help="process the synthetic month into --var before serving")
    ap.add_argument("--reset", action="store_true", help="with --seed: delete the demo state in --var first (cases, audit, egress log)")
    ap.add_argument("--mode", default=None, help="llm mode for new complaints: auto | replay | live")
    args = ap.parse_args()
    var = Path(args.var)
    if args.seed and (var / "cases").exists() and any((var / "cases").glob("*.json")):
        if not args.reset:
            raise SystemExit(f"{var} already holds cases. Serve it as is (drop --seed), or add --reset to start the demo fresh.")
        import shutil
        for name in ("cases", "private"):
            shutil.rmtree(var / name, ignore_errors=True)
        for name in ("audit.jsonl", "egress.jsonl"):
            (var / name).unlink(missing_ok=True)
        print(f"reset demo state in {var}")
    pipe = Pipeline(_load_crm(), var=Path(args.var), mode=args.mode or ("replay" if args.seed else None), cache_dir=CACHE)
    if args.seed:
        seed(pipe)
        if args.mode is None:
            pipe.llm.mode = "auto"
    STATE["pipe"] = pipe
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Meridian workbench on http://127.0.0.1:{args.port}  (model {pipe.llm.model}, llm mode {pipe.llm.mode})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
