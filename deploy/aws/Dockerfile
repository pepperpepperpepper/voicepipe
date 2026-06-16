# Container-image Lambda for the Zwingli/Zwangli dispatch backend.
#
# Build context is the REPO ROOT (see template.yaml DockerContext: ../..),
# so COPY paths are relative to the repo root.
#
# We install only the slim runtime set the dispatcher + STT actually import
# (verified: the import chain pulls fastapi/pydantic/openai/dotenv/click but
# NOT numpy/sounddevice/PortAudio), rather than `pip install .` which would
# drag the full desktop deps into the image.
FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "mangum>=0.17" \
    "openai>=1.0" \
    "pydantic>=2" \
    "python-dotenv>=1.0" \
    "click>=8.0"

# voicepipe package source onto the Lambda task root (which is on PYTHONPATH).
COPY voicepipe ${LAMBDA_TASK_ROOT}/voicepipe

# Bake a complete triggers.json = packaged defaults + the intent verbs that
# `voicepipe setup` normally enables (alarm/timer/dial/navigate/search/open/
# back/home/recents/notifications/quick_settings). Two reasons this is needed
# on Lambda: (1) the read-only filesystem can't materialize the user
# triggers.json the normal loader writes (it silently falls back to a
# trigger-less config), and (2) without the intent verbs the llm_route LLM
# has no intent verb to route "set a timer" to and falls back to `type`.
# Generating from the asset keeps this in sync with the shipped defaults.
RUN python -c "import json,os,pathlib as p; r=p.Path(os.environ['LAMBDA_TASK_ROOT']); c=json.loads((r/'voicepipe/assets/triggers.default.json').read_text()); v=c.setdefault('verbs',{}); [v.setdefault(x,{'type':'builtin','enabled':True}) for x in ('search','open','alarm','timer','dial','navigate','back','home','recents','notifications','quick_settings')]; (r/'triggers.json').write_text(json.dumps(c,indent=2)); print('baked triggers.json + intent verbs')"
ENV VOICEPIPE_TRIGGERS_JSON=${LAMBDA_TASK_ROOT}/triggers.json

# Fail the build loudly if the slim set is missing an import the handler
# needs — cheaper to catch here than at cold start. If this line fails, add
# the missing package to the pip install above.
RUN python -c "import voicepipe.aws_lambda; print('lambda import OK')"

# Mangum-wrapped FastAPI app (warm-ping aware).
CMD ["voicepipe.aws_lambda.handler"]
