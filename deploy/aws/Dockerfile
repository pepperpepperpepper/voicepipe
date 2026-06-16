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

# Fail the build loudly if the slim set is missing an import the handler
# needs — cheaper to catch here than at cold start. If this line fails, add
# the missing package to the pip install above.
RUN python -c "import voicepipe.aws_lambda; print('lambda import OK')"

# Mangum-wrapped FastAPI app (warm-ping aware).
CMD ["voicepipe.aws_lambda.handler"]
