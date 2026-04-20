#!/bin/bash
# scripts/setup_secrets.sh — Store all app secrets in GCP Secret Manager
# Usage: fill in the values below, then run: bash scripts/setup_secrets.sh
# Prerequisites: gcloud CLI authenticated with roles/secretmanager.admin

set -euo pipefail

PROJECT_ID="aiotek-bot"

# ── Fill these in before running ──────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN=""
LINE_CHANNEL_SECRET=""
LIFF_ID=""
LIFF_CHANNEL_ID=""
LIFF_CHANNEL_SECRET=""
DATABASE_URL=""        # postgresql+psycopg2://user:pass@/dbname?host=/cloudsql/aiotek-bot:asia-east1:INSTANCE
MAILGUN_API_KEY=""
MAILGUN_FROM_EMAIL=""
SESSION_SECRET_KEY=""  # generate: openssl rand -hex 32
APP_BASE_URL=""        # https://line-clockio-HASH-de.a.run.app  (fill after first deploy)
# ─────────────────────────────────────────────────────────────

create_secret() {
  local name="$1"
  local value="$2"

  if [ -z "$value" ]; then
    echo "  SKIP  ${name} (empty — fill in the script first)"
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
create_secret "LINE_CHANNEL_ACCESS_TOKEN" "${LINE_CHANNEL_ACCESS_TOKEN}"
create_secret "LINE_CHANNEL_SECRET"       "${LINE_CHANNEL_SECRET}"
create_secret "LIFF_ID"                   "${LIFF_ID}"
create_secret "LIFF_CHANNEL_ID"           "${LIFF_CHANNEL_ID}"
create_secret "LIFF_CHANNEL_SECRET"       "${LIFF_CHANNEL_SECRET}"
create_secret "DATABASE_URL"              "${DATABASE_URL}"
create_secret "MAILGUN_API_KEY"           "${MAILGUN_API_KEY}"
create_secret "MAILGUN_FROM_EMAIL"        "${MAILGUN_FROM_EMAIL}"
create_secret "SESSION_SECRET_KEY"        "${SESSION_SECRET_KEY}"
create_secret "APP_BASE_URL"              "${APP_BASE_URL}"

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
