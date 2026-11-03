#!/usr/bin/env bash
# One immutable bundle per CI attempt; no application secrets or source data.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]]
[[ "${IMAGE_DIGEST:-}" =~ ^sha256:[a-f0-9]{64}$ ]]
[[ "${WEB_IMAGE_DIGEST:-}" =~ ^sha256:[a-f0-9]{64}$ ]]
image="ghcr.io/$(printf '%s' "$GITHUB_REPOSITORY" | tr '[:upper:]' '[:lower:]')"
mkdir -p dist/cloud/deployment
git archive HEAD deploy .env.example scripts/configure_local.py docs/releasing.md docs/traffic-deployment.md docs/backend-validation.md docs/redis-sentinel.md | tar -x -C dist/cloud/deployment
printf 'COMMERCE_IMAGE=%s@%s\nCOMMERCE_WEB_IMAGE=%s-web@%s\nCOMMERCE_REVISION=%s\n' \
  "$image" "$IMAGE_DIGEST" "$image" "$WEB_IMAGE_DIGEST" "$GITHUB_SHA" > dist/cloud/deployment/image.env
tar -czf dist/cloud/a-commerce-deploy.tar.gz -C dist/cloud/deployment .
(cd dist/cloud && sha256sum a-commerce-deploy.tar.gz > SHA256SUMS)
