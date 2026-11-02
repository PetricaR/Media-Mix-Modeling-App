#!/usr/bin/env bash
# ==============================================================================
# MMM Studio — Idempotent Google Cloud Deployment Script (GCP Cloud Run + SQL)
# Target Project: formare-dev (europe-west3 Frankfurt)
# ==============================================================================
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-formare-dev}"
REGION="${REGION:-europe-west3}"
AR_REPO="${AR_REPO:-mmm-studio}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-mmm-studio-artifacts}"
SQL_INSTANCE="${SQL_INSTANCE:-mmm-studio-pg}"
DB_NAME="${DB_NAME:-mmm_studio}"
DB_USER="${DB_USER:-mmm_user}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/api:${IMAGE_TAG}"
WORKER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/worker:${IMAGE_TAG}"
FRONTEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/frontend:${IMAGE_TAG}"

API_SA="mmm-studio-api@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="mmm-studio-worker@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> [1/9] Configuring project ${PROJECT_ID} (${REGION}) and enabling APIs..."
gcloud config set project "${PROJECT_ID}"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com

echo "==> [2/9] Ensuring Artifact Registry (${AR_REPO}) and GCS Bucket (gs://${BUCKET_NAME})..."
if ! gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="MMM Studio container images"
fi

if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

echo "==> [3/9] Ensuring Cloud SQL PostgreSQL 16 instance (${SQL_INSTANCE})..."
if ! gcloud sql instances describe "${SQL_INSTANCE}" >/dev/null 2>&1; then
  gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_16 \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-size=10GB \
    --storage-type=SSD \
    --availability-type=ZONAL
fi

if ! gcloud sql databases describe "${DB_NAME}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1; then
  gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}"
fi

echo "==> [4/9] Ensuring Service Accounts and IAM bindings..."
if ! gcloud iam service-accounts describe "${API_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create mmm-studio-api --display-name="MMM Studio API Service Account"
fi
if ! gcloud iam service-accounts describe "${WORKER_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create mmm-studio-worker --display-name="MMM Studio Worker Service Account"
fi

for SA in "${API_SA}" "${WORKER_SA}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA}" \
    --role="roles/cloudsql.client" --quiet >/dev/null
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --member="serviceAccount:${SA}" \
    --role="roles/storage.objectAdmin" --quiet >/dev/null
done

echo "==> [5/9] Ensuring Secret Manager secrets (mmm-studio-db-url, mmm-studio-access-code)..."
for SECRET_NAME in mmm-studio-db-url mmm-studio-access-code; do
  if ! gcloud secrets describe "${SECRET_NAME}" >/dev/null 2>&1; then
    echo "Creating placeholder secret ${SECRET_NAME} (populate value via gcloud secrets versions add)..."
    printf "changeme" | gcloud secrets create "${SECRET_NAME}" --data-file=-
  fi
done

echo "==> [6/9] Building and pushing Backend API (${API_IMAGE}) and Worker (${WORKER_IMAGE}) images..."
gcloud builds submit src/app/backend \
  --config=/dev/stdin <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '--target', 'api', '-t', '${API_IMAGE}', '.']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '--target', 'worker', '-t', '${WORKER_IMAGE}', '.']
images:
  - '${API_IMAGE}'
  - '${WORKER_IMAGE}'
EOF

echo "==> [7/9] Deploying Cloud Run Worker Jobs (analysis, meridian, interactive)..."
COMMON_ENV="MMM_STORAGE_BACKEND=gcs,MMM_GCS_BUCKET=${BUCKET_NAME},MMM_GCP_PROJECT_ID=${PROJECT_ID},MMM_GCP_REGION=${REGION},MMM_WORKER_ANALYSIS_JOB=mmm-studio-worker-analysis,MMM_WORKER_MERIDIAN_JOB=mmm-studio-worker-meridian,MMM_WORKER_INTERACTIVE_JOB=mmm-studio-worker-interactive"
COMMON_SECRETS="MMM_DATABASE_URL=mmm-studio-db-url:latest,MMM_APP_ACCESS_CODE=mmm-studio-access-code:latest"
SQL_CONN="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"

gcloud run jobs deploy mmm-studio-worker-analysis \
  --image="${WORKER_IMAGE}" \
  --region="${REGION}" \
  --service-account="${WORKER_SA}" \
  --set-cloudsql-instances="${SQL_CONN}" \
  --set-env-vars="${COMMON_ENV}" \
  --set-secrets="${COMMON_SECRETS}" \
  --cpu=2 --memory=4Gi \
  --max-retries=1 \
  --task-timeout=900s \
  --command="uv" \
  --args="run,python,-m,mmm_studio.cli,worker,--queue,analysis,--burst"

gcloud run jobs deploy mmm-studio-worker-meridian \
  --image="${WORKER_IMAGE}" \
  --region="${REGION}" \
  --service-account="${WORKER_SA}" \
  --set-cloudsql-instances="${SQL_CONN}" \
  --set-env-vars="${COMMON_ENV}" \
  --set-secrets="${COMMON_SECRETS}" \
  --cpu=4 --memory=16Gi \
  --max-retries=0 \
  --task-timeout=3600s \
  --command="uv" \
  --args="run,python,-m,mmm_studio.cli,worker,--queue,meridian,--burst"

gcloud run jobs deploy mmm-studio-worker-interactive \
  --image="${WORKER_IMAGE}" \
  --region="${REGION}" \
  --service-account="${WORKER_SA}" \
  --set-cloudsql-instances="${SQL_CONN}" \
  --set-env-vars="${COMMON_ENV}" \
  --set-secrets="${COMMON_SECRETS}" \
  --cpu=2 --memory=8Gi \
  --max-retries=1 \
  --task-timeout=900s \
  --command="uv" \
  --args="run,python,-m,mmm_studio.cli,worker,--queue,interactive,--idle-exit,300"

for JOB_NAME in mmm-studio-worker-analysis mmm-studio-worker-meridian mmm-studio-worker-interactive; do
  gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
    --region="${REGION}" \
    --member="serviceAccount:${API_SA}" \
    --role="roles/run.developer" --quiet >/dev/null
done

echo "==> [8/9] Deploying Cloud Run API Service (mmm-studio-api)..."
gcloud run deploy mmm-studio-api \
  --image="${API_IMAGE}" \
  --region="${REGION}" \
  --service-account="${API_SA}" \
  --set-cloudsql-instances="${SQL_CONN}" \
  --set-env-vars="${COMMON_ENV},MMM_WAKE_WORKERS=cloudrun" \
  --set-secrets="${COMMON_SECRETS}" \
  --cpu=1 --memory=2Gi \
  --min-instances=0 --max-instances=4 \
  --concurrency=40 \
  --timeout=120s \
  --allow-unauthenticated

API_URL="$(gcloud run services describe mmm-studio-api --region="${REGION}" --format='value(status.url)')"
echo "API Service URL: ${API_URL}"

echo "==> [9/9] Building Frontend image (${FRONTEND_IMAGE}) and deploying mmm-studio-web..."
gcloud builds submit src/app \
  --config=/dev/stdin <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', 'frontend/Dockerfile', '-t', '${FRONTEND_IMAGE}', '.']
images:
  - '${FRONTEND_IMAGE}'
EOF

gcloud run deploy mmm-studio-web \
  --image="${FRONTEND_IMAGE}" \
  --region="${REGION}" \
  --set-env-vars="API_ORIGIN=${API_URL}" \
  --cpu=1 --memory=512Mi \
  --min-instances=0 --max-instances=4 \
  --concurrency=80 \
  --timeout=60s \
  --allow-unauthenticated

WEB_URL="$(gcloud run services describe mmm-studio-web --region="${REGION}" --format='value(status.url)')"
echo "=============================================================================="
echo "MMM Studio deployed successfully!"
echo "Web URL: ${WEB_URL}"
echo "API URL: ${API_URL}"
echo "=============================================================================="
