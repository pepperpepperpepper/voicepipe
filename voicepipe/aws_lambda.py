"""AWS Lambda entry point for the Zwingli dispatch server.

Wraps :func:`voicepipe.dispatch_server.create_app` with Mangum so the
*same* FastAPI app that runs under uvicorn (spermwhale.info, Lightsail,
Fargate) also runs behind a Lambda Function URL or API Gateway. The
dispatcher brain is host-agnostic, so this is purely a packaging adapter.

Deploy::

    pip install 'voicepipe[aws]'
    # handler reference for Lambda:  voicepipe.aws_lambda.handler

The Lambda's environment supplies the secrets/config the app reads at
import time (cold start): VOICEPIPE_DISPATCH_TOKEN (auth), OPENAI_API_KEY /
GROQ_API_KEY (STT + routing), and VOICEPIPE_DISPATCH_STT_MODEL if you want
to repoint STT off the Groq Whisper default.

Caveats specific to Lambda (the brain is otherwise stateless):

- ``POST /transcribe-dispatch`` and ``POST /dispatch`` are read-only on
  config and Lambda-safe.
- ``PATCH /triggers`` and ``GET /log/tail`` touch the local filesystem,
  which is ephemeral and per-instance on Lambda — don't rely on them in a
  serverless deploy (manage triggers.json at build/deploy time instead).
- The Zwingli rate limiter is an in-process counter and does not survive
  cold starts or span concurrent instances.
- Binary audio uploads arrive base64-encoded; Mangum decodes the request
  body automatically. A Lambda Function URL caps the body at ~6MB (≈4.5MB
  raw after base64), so keep clips short/compressed.
"""
from __future__ import annotations

from voicepipe.dispatch_server import create_app

try:
    from mangum import Mangum
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise RuntimeError(
        "voicepipe AWS Lambda entry requires the 'aws' extra: "
        "pip install 'voicepipe[aws]'"
    ) from e


# Built once per cold start and reused across warm invocations. create_app()
# resolves VOICEPIPE_DISPATCH_TOKEN from the Lambda environment.
app = create_app()

# Lambda handler reference: ``voicepipe.aws_lambda.handler``.
handler = Mangum(app)
