#!/usr/bin/env bash
# Deploy the Zwingli/Zwangli dispatch backend to AWS Lambda (container image +
# Function URL + keep-warm). Secrets are read from the environment and passed
# via --parameter-overrides; they are NOT written to samconfig.toml.
#
# Usage:
#   source ~/.api-keys                       # exposes GROQ_API_KEY / OPENAI_API_KEY
#   export VOICEPIPE_DISPATCH_TOKEN=$(openssl rand -hex 32)   # save this; the app needs it
#   ./deploy/aws/deploy.sh
#
# Prereqs: AWS CLI + SAM CLI + Docker, and configured AWS credentials.
set -euo pipefail

: "${VOICEPIPE_DISPATCH_TOKEN:?set VOICEPIPE_DISPATCH_TOKEN — the client bearer token}"
: "${GROQ_API_KEY:?set GROQ_API_KEY (e.g. \`source ~/.api-keys\`)}"

OPENAI_API_KEY="${OPENAI_API_KEY:-}"
STT_MODEL="${VOICEPIPE_DISPATCH_STT_MODEL:-groq:whisper-large-v3-turbo}"
REGION="${AWS_REGION:-us-east-1}"          # near OpenAI/Groq endpoints
STACK="${ZWANGLI_STACK_NAME:-zwangli-dispatch}"
# Google Sign-In (optional until the OAuth Web client ID exists). Set
# GOOGLE_OAUTH_CLIENT_ID to enable; ZWANGLI_ALLOW_STATIC_TOKEN=1 keeps the
# legacy bearer as break-glass during cutover (set empty for Google-only).
GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-}"
ALLOWED_EMAIL="${ZWANGLI_ALLOWED_EMAIL:-peppersclothescult@gmail.com}"
ALLOW_STATIC_TOKEN="${ZWANGLI_ALLOW_STATIC_TOKEN:-1}"

cd "$(dirname "$0")"

echo ">> sam build (region=${REGION}, stack=${STACK})"
sam build

echo ">> sam deploy"
sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --resolve-image-repos \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "DispatchToken=${VOICEPIPE_DISPATCH_TOKEN}" \
    "GroqApiKey=${GROQ_API_KEY}" \
    "OpenAiApiKey=${OPENAI_API_KEY}" \
    "SttModel=${STT_MODEL}" \
    "GoogleOauthClientId=${GOOGLE_OAUTH_CLIENT_ID}" \
    "AllowedEmail=${ALLOWED_EMAIL}" \
    "AllowStaticToken=${ALLOW_STATIC_TOKEN}"

echo ">> Function URL:"
aws cloudformation describe-stacks \
  --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" \
  --output text
