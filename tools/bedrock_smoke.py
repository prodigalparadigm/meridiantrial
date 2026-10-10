"""One Bedrock invoke, standard library only.

The rest of this project depends on nothing outside the standard library, and
this script keeps that property: it signs the request with SigV4 by hand rather
than pulling in boto3/botocore. Run it to confirm the account can reach a Claude
model on Bedrock.

    python3 tools/bedrock_smoke.py
    python3 tools/bedrock_smoke.py --model us.anthropic.claude-haiku-4-5-20251001-v1:0

Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (plus
AWS_SESSION_TOKEN if set), falling back to the default profile in
~/.aws/credentials. Region comes from --region, AWS_REGION, or ~/.aws/config.

Note on model ids: every Anthropic model in this account's Bedrock catalogue is
INFERENCE_PROFILE-only, so the id needs the geo prefix ("us.anthropic.…"), not
the bare "anthropic.…" that ListFoundationModels reports.
"""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVICE = "bedrock"
DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _credentials() -> tuple[str, str, str | None]:
    ak, sk = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
    if ak and sk:
        return ak, sk, os.environ.get("AWS_SESSION_TOKEN")
    path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws/credentials"))
    profile = os.environ.get("AWS_PROFILE", "default")
    cp = configparser.ConfigParser()
    cp.read(path)
    if profile not in cp:
        raise SystemExit(f"no credentials: set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or add [{profile}] to {path}")
    s = cp[profile]
    return s["aws_access_key_id"], s["aws_secret_access_key"], s.get("aws_session_token")


def _region(explicit: str | None) -> str:
    if explicit:
        return explicit
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(var):
            return os.environ[var]
    cp = configparser.ConfigParser()
    cp.read(Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws/config")))
    profile = os.environ.get("AWS_PROFILE", "default")
    for section in (profile, f"profile {profile}"):
        if section in cp and cp[section].get("region"):
            return cp[section]["region"]
    raise SystemExit("no region: pass --region or set AWS_REGION")


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def converse(model: str, prompt: str, region: str, max_tokens: int = 64) -> dict:
    """POST to bedrock-runtime /converse, signed with SigV4."""
    ak, sk, token = _credentials()
    host = f"bedrock-runtime.{region}.amazonaws.com"
    # The model id contains a colon, which has to be percent-encoded in the path.
    # SigV4 then wants the canonical path encoded a *second* time for every
    # service except S3, so the colon appears as %3A in the URL we request and
    # as %253A in the string we sign. Signing the single-encoded path is the
    # mistake that earns a 403 SignatureDoesNotMatch here.
    quoted = urllib.parse.quote(model, safe="")
    uri = f"/model/{quoted}/converse"
    canonical_uri = f"/model/{urllib.parse.quote(quoted, safe='')}/converse"
    body = json.dumps({
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }).encode()

    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {"content-type": "application/json", "host": host, "x-amz-date": amz_date}
    if token:
        headers["x-amz-security-token"] = token
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(["POST", canonical_uri, "", canonical_headers, signed_headers, payload_hash])

    scope = f"{datestamp}/{region}/{SERVICE}/aws4_request"
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    k = _sign(f"AWS4{sk}".encode(), datestamp)
    for part in (region, SERVICE, "aws4_request"):
        k = _sign(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    req = urllib.request.Request(f"https://{host}{uri}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"Bedrock returned HTTP {e.code}: {detail}") from None


def main() -> int:
    ap = argparse.ArgumentParser(description="One Bedrock invoke, no third-party packages.")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--region", default=None)
    ap.add_argument("--prompt", default="Reply with exactly: BEDROCK_OK")
    args = ap.parse_args()

    region = _region(args.region)
    out = converse(args.model, args.prompt, region)
    text = "".join(b.get("text", "") for b in out["output"]["message"]["content"])
    u = out.get("usage", {})
    print(f"model   {args.model}")
    print(f"region  {region}")
    print(f"reply   {text.strip()}")
    print(f"tokens  {u.get('inputTokens')} in / {u.get('outputTokens')} out")
    print(f"stop    {out.get('stopReason')}  latency {out.get('metrics', {}).get('latencyMs')}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
