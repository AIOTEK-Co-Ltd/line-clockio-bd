#!/bin/bash
# deploy.sh — Build and deploy to GCP Cloud Run
# Usage: ./deploy.sh
# Prerequisites: gcloud CLI authenticated, Docker running

set -euo pipefail

PROJECT_ID="aiotek-bot"
REGION="asia-east1"
SERVICE="line-clockio"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"

echo "==> Building Docker image..."
docker build -t "${IMAGE}" .

echo "==> Pushing to Google Container Registry..."
docker push "${IMAGE}"

echo "==> Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 10 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 60 \
  --add-cloudsql-instances "${PROJECT_ID}:${REGION}:line-clockio-db" \
  --set-secrets "\
LINE_CHANNEL_ACCESS_TOKEN=LINE_CHANNEL_ACCESS_TOKEN:latest,\
LINE_CHANNEL_SECRET=LINE_CHANNEL_SECRET:latest,\
LIFF_ID=LIFF_ID:latest,\
LIFF_CHANNEL_ID=LIFF_CHANNEL_ID:latest,\
LIFF_CHANNEL_SECRET=LIFF_CHANNEL_SECRET:latest,\
DATABASE_URL=DATABASE_URL:latest,\
MAILGUN_API_KEY=MAILGUN_API_KEY:latest,\
MAILGUN_FROM_EMAIL=MAILGUN_FROM_EMAIL:latest,\
SESSION_SECRET_KEY=SESSION_SECRET_KEY:latest,\
APP_BASE_URL=APP_BASE_URL:latest"

echo ""
echo "==> Deploy complete."
echo "    Service URL:"
gcloud run services describe "${SERVICE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)"
