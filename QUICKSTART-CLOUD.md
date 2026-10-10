# QUICKSTART-CLOUD — the workbench on a public URL

The stakeholder walkthrough, running on AWS App Runner from a private ECR image.
Stood up 2026-10-09. **Tear it down Tuesday 2026-10-13** — commands at the bottom.

## The URL

**https://gb5x3fdd2e.us-east-1.awsapprunner.com**

| Path | What it is |
|---|---|
| `/` | the workbench UI — analyst queues, case detail, drafts |
| `/walkthrough` | the offline stakeholder page (same as `docs/walkthrough.html`) |
| `/api/stats` | queue counts, flag latency, per-department and per-policy breakdown |
| `/api/audit/verify` | re-walks the audit hash chain and reports `ok` |

It serves the recorded synthetic month: **163 complaints, all on `awaiting_analyst`,
106 flagged** — identical to `python3 -m meridian.server --seed` locally. Verified
end to end after deploy: UI 200, `/walkthrough` 200, 163 cases over `/api/cases`,
and the audit chain verifying at 975 entries.

> **No authentication.** Anyone with the link can read every case and click
> approve, reroute and compliance-acknowledge. The data is wholly synthetic
> (`data/README.md`), so this is a demo-hygiene matter rather than a privacy one —
> but do not treat the URL as private, and do not point it at real complaints.

## What was created

Account **882597580767**, region **us-east-1**, all of it tagged
`Project=meridian-workbench`, `Purpose=stakeholder-demo`, `TeardownBy=2026-10-13`.

| Resource | Identifier |
|---|---|
| ECR repository (private) | `882597580767.dkr.ecr.us-east-1.amazonaws.com/meridian-workbench` |
| Image | tags `v1` + `latest`, digest `sha256:4c2840432f4de127adb4ff8312f2c72b2637511be807c656ed1605c103a7aeea`, 45.4 MB |
| App Runner service | `arn:aws:apprunner:us-east-1:882597580767:service/meridian-workbench/f609cad54ba44412a3ba588dc3099562` |
| Autoscaling config | `arn:aws:apprunner:us-east-1:882597580767:autoscalingconfiguration/meridian-single/1/87fbd1e91e974d12b2b485db005603f0` (min 1, max 1) |
| IAM role | `AppRunnerECRAccessRole-meridian` — lets App Runner pull from the private repo |
| CloudWatch log groups | `/aws/apprunner/meridian-workbench/f609cad54ba44412a3ba588dc3099562/{service,application}` |

Service shape: **0.25 vCPU / 0.5 GB** (the smallest App Runner offers), **one
instance, no autoscaling**, auto-deployments **off** (a new image push does not
redeploy; you trigger it), health check HTTP `GET /` every 10s.

## What it costs

Provisioned memory is the only thing billed by the hour at rest: 0.5 GB ×
$0.007/GB-hr = **$0.0035/hr ≈ $0.084/day**, so **~$0.34** for Thursday→Tuesday.
Active CPU adds $0.016/hr but only while requests are in flight — a few hours of
demo traffic is cents. ECR storage for a 45 MB image is ~$0.005/month. **Total
expected: well under $1.** Billing stops when the service is deleted, not paused.

## Rebuilding and redeploying

```bash
# build (linux/amd64 is required - App Runner is x86_64 only)
docker build --platform linux/amd64 -t meridian-workbench:local .

# push. --provenance=false matters: buildkit's default attestation produces an
# OCI image index with an unknown/unknown entry, which App Runner rejects.
REPO=882597580767.dkr.ecr.us-east-1.amazonaws.com/meridian-workbench
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 882597580767.dkr.ecr.us-east-1.amazonaws.com
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t "${REPO}:v1" --push .

# roll the service onto the new image
aws apprunner start-deployment --region us-east-1 --service-arn \
  arn:aws:apprunner:us-east-1:882597580767:service/meridian-workbench/f609cad54ba44412a3ba588dc3099562
```

In zsh, always brace the tag — bare `$REPO:latest` is read as the `:l`
lowercase modifier plus the literal `atest`, and pushes to a repo that
does not exist.

## Three things worth knowing

**The image carries a wordlist on purpose.** `meridian/redact.py:58` reads
`/usr/share/dict/words` to tell an ordinary capitalized English word from a
possible name. macOS ships that file; `python:3.12-slim` does not, and with no
dictionary the redactor fails safe by treating *every* unknown capitalized word
as a name. That over-redacts (`CX CALLING RE…` → `CX ⟦NAME_1⟧ RE…`), the payload
stops matching the recorded response hashes, replay misses, and **69 of 163
complaints fall through to manual triage** — a half-empty queue in front of the
stakeholders. The Dockerfile pulls the same `web2` list from Debian `miscfiles`
in a throwaway stage and asserts at build time that all 163 cases land on
`awaiting_analyst`. If that assertion ever fires, the redaction inputs moved.
This dependency is not declared anywhere else in the repo, and the redaction
numbers in `docs/EVIDENCE.md` were measured against the macOS copy of it.

**The month is seeded at build time, not startup.** Seeding costs ~15s locally
and longer on a 0.25 vCPU instance, which would race the health check. The image
ships `/app/var` already populated, so the container serves within ~2s.

**Demo state is ephemeral.** Approvals, reroutes and compliance acknowledgements
write to the container's own `/app/var`. Any restart or redeploy returns the
service to the pristine seeded month — nothing a stakeholder clicks survives.
That is fine for a walkthrough; just do not treat a sign-off as recorded.

## Local runs are unchanged

`server.py` now takes `--host` (default `127.0.0.1`, or `MERIDIAN_HOST`), and
`--port` reads `PORT` when the platform sets it. Both defaults are the old
behaviour, so this still binds to loopback only:

```bash
python3 -m meridian.server --seed --port 8770
```

The container sets `MERIDIAN_HOST=0.0.0.0` because it has to listen on all
interfaces. Nothing else about the server changed.

## Bedrock

**Not enabled on this account — the stretch goal is not done.** `us-east-1` lists
the Anthropic catalogue (Haiku 4.5, Opus 5.5, Fable 5, Sonnet 4), and one probe
invoke of `us.anthropic.claude-haiku-4-5-20251001-v1:0` did return successfully.
Every call after that — through both the AWS CLI and the script below — returns:

```
ResourceNotFoundException: Model use case details have not been submitted for
this account. Fill out the Anthropic use case details form before using the model.
```

The account's own pinned model, `anthropic.claude-opus-5-5` (`config.MODEL`), is
separately denied: *not available for this account*. Submitting the use-case form
would be enabling access, which was explicitly out of scope, so nothing was
changed. `tools/bedrock_smoke.py` is ready for whenever access lands:

```bash
python3 tools/bedrock_smoke.py
python3 tools/bedrock_smoke.py --model us.anthropic.claude-haiku-4-5-20251001-v1:0
```

It is standard library only — no boto3 — to keep the project's zero-dependency
property, and signs SigV4 by hand. Its signing is confirmed correct: requests
reach Bedrock and come back with a service-level entitlement error, not a 403.

## Teardown — run this Tuesday

Deleting the service is what stops the billing; everything after it is tidying.

```bash
REG=us-east-1
SVC=arn:aws:apprunner:us-east-1:882597580767:service/meridian-workbench/f609cad54ba44412a3ba588dc3099562

# 1. the service (this is the only hourly charge)
aws apprunner delete-service --region $REG --service-arn $SVC

# wait for it to go away before deleting the autoscaling config it references
while aws apprunner describe-service --region $REG --service-arn $SVC \
      --query Service.Status --output text 2>/dev/null; do sleep 20; done

# 2. the autoscaling configuration
aws apprunner delete-auto-scaling-configuration --region $REG \
  --auto-scaling-configuration-arn \
  arn:aws:apprunner:us-east-1:882597580767:autoscalingconfiguration/meridian-single/1/87fbd1e91e974d12b2b485db005603f0

# 3. the image repository, images and all
aws ecr delete-repository --region $REG --repository-name meridian-workbench --force

# 4. the IAM role (detach first, or the delete is refused)
aws iam detach-role-policy --role-name AppRunnerECRAccessRole-meridian \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
aws iam delete-role --role-name AppRunnerECRAccessRole-meridian

# 5. the log groups (they outlive the service and bill for storage)
aws logs delete-log-group --region $REG --log-group-name \
  /aws/apprunner/meridian-workbench/f609cad54ba44412a3ba588dc3099562/service
aws logs delete-log-group --region $REG --log-group-name \
  /aws/apprunner/meridian-workbench/f609cad54ba44412a3ba588dc3099562/application
```

Confirm nothing is left:

```bash
aws apprunner list-services --region us-east-1 --query 'ServiceSummaryList[].ServiceName'
aws ecr describe-repositories --region us-east-1 --query 'repositories[].repositoryName'
aws iam list-roles --query "Roles[?starts_with(RoleName,'AppRunnerECRAccessRole-meridian')].RoleName"
```
