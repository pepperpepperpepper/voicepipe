# Zwangli dispatch backend on AWS Lambda

Deploys `voicepipe.dispatch_server` as a **container-image Lambda** behind a
**Function URL** (managed HTTPS), kept warm by an **EventBridge ping** so the
first "speak" after an idle gap doesn't pay a cold start. Auth in v1 is the
app-level bearer token (`VOICEPIPE_DISPATCH_TOKEN`); the Function URL is
`AuthType NONE` so the `Authorization` header reaches the app.

```
Android ──HTTPS──▶ Function URL ──▶ Lambda (Mangum→FastAPI)
                                      POST /transcribe-dispatch
                                        ├─ Groq Whisper STT (whisper-large-v3-turbo)
                                        └─ dispatcher → client_actions
EventBridge rate(5 min) ──{"warmer":true}──▶ same Lambda (short-circuits, stays hot)
```

## Prerequisites

- AWS CLI + [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) + Docker
- AWS credentials configured (`aws sts get-caller-identity` works)
- Region `us-east-1` (close to the OpenAI/Groq endpoints — lowest STT/LLM RTT)

## Deploy

```bash
source ~/.api-keys                                  # exposes GROQ_API_KEY (and OPENAI_API_KEY)
export VOICEPIPE_DISPATCH_TOKEN=$(openssl rand -hex 32)
echo "SAVE THIS TOKEN: $VOICEPIPE_DISPATCH_TOKEN"   # the Android app sends it as the bearer
./deploy/aws/deploy.sh
```

The script prints the **Function URL** at the end. Set that as the Zwangli
server URL and the token above as the bearer token in the app's configurator.

## Smoke test

```bash
URL=<the Function URL>
TOKEN=<the token you saved>

# health (no auth required)
curl -s "${URL%/}/health"

# audio round-trip: any short wav/mp3/m4a clip
curl -s -X POST "${URL%/}/transcribe-dispatch" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @clip.m4a
# → {"ok":true,"transcript":"...","output_text":"...","client_actions":[...]}
```

## Config knobs (Lambda env vars / template parameters)

| Parameter | Env var | Default | Notes |
|---|---|---|---|
| `DispatchToken` | `VOICEPIPE_DISPATCH_TOKEN` | — (required) | client bearer token |
| `GroqApiKey` | `GROQ_API_KEY` | — (required) | Groq Whisper STT + LLM router |
| `OpenAiApiKey` | `OPENAI_API_KEY` | "" | only if STT/LLM repointed to OpenAI |
| `SttModel` | `VOICEPIPE_DISPATCH_STT_MODEL` | `groq:whisper-large-v3-turbo` | STT model |
| `KeepWarmRate` | — | `rate(5 minutes)` | EventBridge ping cadence |

## Notes & caveats

- **Cost:** ~$0/mo. Lambda compute + Function URL + the keep-warm pings all sit
  in the perpetual free tier at single-user volume.
- **Cold start:** the keep-warm ping holds one execution environment hot. A
  *second concurrent* request would still cold-start, but a single user is ~1
  concurrency.
- **Payload cap:** a Function URL caps the request body at ~6 MB (≈4.5 MB raw
  after base64), and the app caps at 15 MB (`VOICEPIPE_DISPATCH_MAX_AUDIO_BYTES`).
  Keep clips short/compressed (m4a/Opus).
- **Secrets:** passed via `--parameter-overrides` (NoEcho) and **not** persisted
  to `samconfig.toml` (gitignored). Hardening step: move to SSM/Secrets Manager
  read at runtime.
- **Filesystem endpoints:** `PATCH /triggers` and `GET /log/tail` write/read the
  ephemeral per-instance Lambda FS — don't rely on them serverless; manage
  `triggers.json` at build time. `/dispatch` and `/transcribe-dispatch` are
  read-only on config and Lambda-safe.
- **Apple Silicon:** the template targets `x86_64`. Building on arm64 needs
  `--platform linux/amd64` (emulation) or switch `Architectures` to `arm64`.
- **Auth v1:** static bearer token. Google Sign-In (ID-token verification at the
  existing `_check_auth` seam) is the planned next step.
```
