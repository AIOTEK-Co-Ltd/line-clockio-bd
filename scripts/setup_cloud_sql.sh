#!/bin/bash
# scripts/setup_cloud_sql.sh — Create and configure Cloud SQL PostgreSQL instance
# Usage: bash scripts/setup_cloud_sql.sh
# Prerequisites: gcloud CLI authenticated with roles/cloudsql.admin

set -euo pipefail

PROJECT_ID="aiotek-bot"
REGION="asia-east1"
INSTANCE="line-clockio-db"
DB_NAME="clockio"
DB_USER="clockio"

# ── Fill these in before running ──────────────────────────────
DB_PASSWORD=""   # choose a strong password
# ─────────────────────────────────────────────────────────────

if [ -z "$DB_PASSWORD" ]; then
  echo "ERROR: set DB_PASSWORD in this script before running."
  exit 1
fi

echo "==> [1/5] Enabling Cloud SQL Admin API..."
gcloud services enable sqladmin.googleapis.com --project="${PROJECT_ID}"

echo "==> [2/5] Creating Cloud SQL instance (PostgreSQL 15)..."
gcloud sql instances create "${INSTANCE}" \
  --project="${PROJECT_ID}" \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-type=SSD \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=02:00 \
  --no-assign-ip \
  --enable-google-private-path

echo "==> [3/5] Creating database..."
gcloud sql databases create "${DB_NAME}" \
  --instance="${INSTANCE}" \
  --project="${PROJECT_ID}"

echo "==> [4/5] Creating database user..."
gcloud sql users create "${DB_USER}" \
  --instance="${INSTANCE}" \
  --project="${PROJECT_ID}" \
  --password="${DB_PASSWORD}"

echo "==> [5/5] Done. Add these to scripts/setup_secrets.sh:"
echo ""
echo "    DATABASE_URL=\"postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${PROJECT_ID}:${REGION}:${INSTANCE}\""
echo ""
echo "    Connection name (for Cloud Run --add-cloudsql-instances flag):"
echo "    ${PROJECT_ID}:${REGION}:${INSTANCE}"
echo ""
echo "==> Add Cloud SQL connection to Cloud Run deploy (already in deploy.sh):"
echo "    Make sure deploy.sh includes:"
echo "    --add-cloudsql-instances ${PROJECT_ID}:${REGION}:${INSTANCE} \\"
