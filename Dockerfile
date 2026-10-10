# Meridian Harbor complaint-intake workbench - demo image for the stakeholder
# walkthrough. The server and UI are standard library only, so there is no
# requirements file and nothing to pip install.

# --------------------------------------------------------------- wordlist
# redact.py reads /usr/share/dict/words to decide whether a capitalized word is
# an ordinary English word or a possible name (meridian/redact.py:58). With no
# dictionary it fails safe by treating every unknown capitalized word as a name,
# which over-redacts: the payload stops matching the recorded responses and 69
# of the 163 complaints fall through to manual triage. miscfiles ships the same
# web2 list the month was recorded against. It is fetched in a throwaway stage
# because the package depends on dictionaries-common, which drags in perl -
# about 50 MB the runtime image has no use for. Only the list itself crosses.
FROM python:3.12-slim AS dict
RUN apt-get update \
 && apt-get install -y --no-install-recommends miscfiles \
 && rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------- runtime
FROM python:3.12-slim

COPY --from=dict /usr/share/dict/web2 /usr/share/dict/web2
RUN ln -sf /usr/share/dict/web2 /usr/share/dict/words \
 && useradd --create-home --uid 10001 meridian \
 && mkdir -p /app && chown meridian:meridian /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIDIAN_VAR=/app/var \
    MERIDIAN_HOST=0.0.0.0 \
    PORT=8770

WORKDIR /app
# Unprivileged from here down, so the seeded state below is written as the user
# that serves it. var/ stays writable because analyst actions append to the
# audit chain - that state is per-container and resets on restart.
USER meridian

# Only what the server reads at runtime: no tests, no held-out set, no recorded
# eval runs, and not the local var/ - the demo state is built below instead.
COPY --chown=meridian:meridian meridian/ meridian/
COPY --chown=meridian:meridian config/ config/
COPY --chown=meridian:meridian ui/ ui/
COPY --chown=meridian:meridian docs/walkthrough.html docs/walkthrough.html
COPY --chown=meridian:meridian data/synthetic/complaints.jsonl data/synthetic/crm.json data/synthetic/
COPY --chown=meridian:meridian evals/llm_cache/ evals/llm_cache/

# Seed the synthetic month at build time from the recorded model responses.
# Doing it here rather than at startup means the container begins serving at
# once, instead of spending its first minute processing 163 complaints on a
# 0.25 vCPU instance while the health check watches the port.
#
# The assertion is the real guard: every case must land on awaiting_analyst.
# Anything that perturbs the redacted payload - a missing dictionary, a changed
# word list, a prompt edit - shows up as a replay cache miss and a case in
# manual_triage, and fails the build here rather than surfacing as a half-empty
# queue in front of the stakeholders.
RUN python -c "import os, pathlib; from meridian.server import seed, _load_crm, CACHE; from meridian.pipeline import Pipeline; seed(Pipeline(_load_crm(), var=pathlib.Path(os.environ['MERIDIAN_VAR']), mode='replay', cache_dir=CACHE))" \
 && python -c "import collections, json, pathlib; from meridian.redact import ENGLISH; st = collections.Counter(json.loads(f.read_text())['status'] for f in pathlib.Path('/app/var/cases').glob('*.json')); print('dictionary:', len(ENGLISH), 'words'); print('seeded:', dict(st)); assert len(ENGLISH) > 200000, f'dictionary not loaded: {len(ENGLISH)} words'; assert st == collections.Counter({'awaiting_analyst': 163}), f'expected 163 awaiting_analyst, got {dict(st)}'"

EXPOSE 8770

# No --seed: the state is already in the image. Mode resolves to "auto", which
# replays the recorded month from cache; a complaint typed into the UI that is
# not in the cache fails closed to manual triage unless ANTHROPIC_API_KEY is set.
CMD ["python", "-m", "meridian.server"]
