#!/bin/bash
# scripts/setup_secrets.sh — Store all app secrets in GCP Secret Manager
# Usage:
#   1. Copy scripts/.secrets.env.example to scripts/.secrets.env
#   2. Fill in values in scripts/.secrets.env (it is gitignored)
#   3. Run: bash scripts/setup_secrets.sh
# Prerequisites: gcloud CLI authenticated with roles/secretmanager.admin

set -euo pipefail

PROJECT_ID="aiotek-bot"

SECRETS_FILE="$(dirname "$0")/.secrets.env"
if [ ! -f "${SECRETS_FILE}" ]; then
  echo "ERROR: ${SECRETS_FILE} not found."
  echo "       Copy scripts/.secrets.env.example to scripts/.secrets.env and fill in values."
  exit 1
fi

# shellcheck source=scripts/.secrets.env
source "${SECRETS_FILE}"

create_secret() {
  local name="$1"
  local value="${!name:-}"

  if [ -z "$value" ]; then
    echo "  SKIP  ${name} (empty in .secrets.env)"
    return
  fi

  if gcloud secrets describe "${name}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  UPDATE ${name}"
    echo -n "${value}" | gcloud secrets versions add "${name}" \
      --project="${PROJECT_ID}" --data-file=-
  else
    echo "  CREATE ${name}"
    echo -n "${value}" | gcloud secrets create "${name}" \
      --project="${PROJECT_ID}" --replication-policy="automatic" --data-file=-
  fi
}

echo "==> Storing secrets in project: ${PROJECT_ID}"
create_secret "LINE_CHANNEL_ACCESS_TOKEN"
create_secret "LINE_CHANNEL_SECRET"
create_secret "LIFF_ID"
create_secret "LIFF_CHANNEL_ID"
create_secret "LIFF_CHANNEL_SECRET"
create_secret "DATABASE_URL"
create_secret "MAILGUN_API_KEY"
create_secret "MAILGUN_FROM_EMAIL"
create_secret "SESSION_SECRET_KEY"
create_secret "APP_BASE_URL"

echo ""
echo "==> Done. Grant Cloud Run access to these secrets:"
echo ""
echo "    # Get the Cloud Run service account:"
echo "    gcloud run services describe line-clockio \\"
echo "      --region asia-east1 --project ${PROJECT_ID} \\"
echo "      --format 'value(spec.template.spec.serviceAccountName)'"
echo ""
echo "    # Then grant secretAccessor to that account:"
echo "    gcloud projects add-iam-policy-binding ${PROJECT_ID} \\"
echo "      --member='serviceAccount:SERVICE_ACCOUNT_EMAIL' \\"
echo "      --role='roles/secretmanager.secretAccessor'"
