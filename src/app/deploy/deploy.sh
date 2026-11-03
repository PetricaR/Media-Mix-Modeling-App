#!/bin/bash

# MMM Studio — Cloud Run deployment
# =================================
# APIs → service accounts → bucket → Cloud SQL → images (Cloud Build) → worker jobs → service → seeded run.
# Safe to re-run; existing resources are reused. Needs no local Docker.
#
# One Cloud Run service (mmm-studio) runs two containers: nginx serves the SPA and proxies /api/ to the
# FastAPI sidecar on localhost:8000, which is never exposed on its own. Three Cloud Run Jobs run the
# Procrastinate workers (analysis, meridian, interactive); the API starts an execution of a queue's job
# each time it defers work on that queue, so nothing runs while the app is idle except Cloud SQL.
#
# Usage (from any directory):
#   GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh          Everything, then seed one run if none exists
#   GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh update   Rebuild images, roll out the jobs and service
#   GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh seed     Upload the case-study workbook and start a run
#
# Environment variables:
#   GCP_PROJECT_ID     GCP project (required; never taken from the active gcloud configuration)
#   GCP_REGION         Region for Cloud Run, Cloud SQL, the bucket and Artifact Registry (default: europe-west3)
#   APP_ACCESS_CODE    Require this code for uploads, runs, forecasts, optimiser runs and deletes. Stored in
#                      Secret Manager (mmm-studio-access-code); without it or that secret, the app is open
#   SEED_PROFILE       Sampling profile of the seeded run: full (default) | quick | smoke
#   SEED_WORKBOOK      Workbook to seed with (default: data/DataScienceMarketingUseCase.xlsx in the repo)
#   ASSUME_YES         Set to "1" to skip the confirmation prompt

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-europe-west3}"
SERVICE_NAME="mmm-studio"
AR_REPOSITORY="mmm-studio"
BUCKET_NAME="${PROJECT_ID}-mmm-studio-artifacts"
SQL_INSTANCE="mmm-studio-pg"
SQL_TIER="db-f1-micro"
DB_NAME="mmm_studio"
DB_USER="mmm_user"
DB_URL_SECRET="mmm-studio-database-url"
ACCESS_CODE_SECRET="mmm-studio-access-code"
API_SA_EMAIL="mmm-studio-api@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA_EMAIL="mmm-studio-worker@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA_EMAIL="mmm-studio-build@${PROJECT_ID}.iam.gserviceaccount.com"
SEED_PROFILE="${SEED_PROFILE:-full}"
STAMP="$(date +%Y%m%d-%H%M%S)"

# Paths — script lives in src/app/deploy/
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_CONTEXT="$(cd "$DEPLOY_DIR/.." && pwd)"       # src/app/
REPO_ROOT="$(cd "$BUILD_CONTEXT/../.." && pwd)"
SEED_WORKBOOK="${SEED_WORKBOOK:-$REPO_ROOT/data/DataScienceMarketingUseCase.xlsx}"
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# Worker jobs: name, queue, CPU, memory, task timeout, worker flags. Sizes follow PLAN.md section 3.4; the
# Meridian job gets 8 vCPU because Cloud Run's x86 CPUs sample slower than the laptop the timings came from.
WORKER_JOBS="
mmm-studio-worker-analysis analysis 2 4Gi 1800s --burst
mmm-studio-worker-meridian meridian 8 16Gi 14400s --burst
mmm-studio-worker-interactive interactive 4 8Gi 1800s --idle-exit,300
"

# ── Helpers ───────────────────────────────────────────────────────────────────
print_step()    { echo -e "\n${CYAN}▶ STEP $1: $2${NC}\n"; }
print_info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }

read_secret() {
    gcloud secrets versions access latest --secret="$1" --project="$PROJECT_ID" 2>/dev/null || true
}

secret_exists() {
    gcloud secrets describe "$1" --project="$PROJECT_ID" &>/dev/null
}

# Stores a new value as the secret's latest version, creating the secret if needed (value on stdin)
write_secret() {
    if secret_exists "$1"; then
        gcloud secrets versions add "$1" --data-file=- --project="$PROJECT_ID" >/dev/null
    else
        gcloud secrets create "$1" --data-file=- --project="$PROJECT_ID" >/dev/null
    fi
}

sql_connection() {
    gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(connectionName)'
}

service_url() {
    gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" \
        --format='value(status.url)'
}

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
check_prerequisites() {
    print_step 1 "Checking prerequisites"
    if [ -z "$PROJECT_ID" ]; then
        print_error "GCP_PROJECT_ID is required (the active gcloud project is never used)"
        exit 1
    fi
    for cmd in gcloud openssl curl python3; do
        if ! command -v "$cmd" &>/dev/null; then
            print_error "$cmd is not installed"
            exit 1
        fi
    done
    if ! gcloud auth print-access-token &>/dev/null; then
        print_error "gcloud has no valid credentials — run: gcloud auth login"
        exit 1
    fi
    if ! gcloud projects describe "$PROJECT_ID" &>/dev/null; then
        print_error "Project $PROJECT_ID is not accessible with $(gcloud config get-value account 2>/dev/null)"
        exit 1
    fi
    print_success "gcloud authenticated as $(gcloud config get-value account 2>/dev/null)"
}

# ── Step 2: GCP APIs ──────────────────────────────────────────────────────────
enable_apis() {
    print_step 2 "Enabling APIs ($PROJECT_ID)"
    gcloud services enable \
        run.googleapis.com \
        sqladmin.googleapis.com \
        artifactregistry.googleapis.com \
        cloudbuild.googleapis.com \
        secretmanager.googleapis.com \
        storage.googleapis.com \
        iam.googleapis.com \
        cloudresourcemanager.googleapis.com \
        logging.googleapis.com \
        monitoring.googleapis.com \
        --project="$PROJECT_ID"
    print_success "APIs enabled"
}

# ── Step 3: Service accounts ──────────────────────────────────────────────────
ensure_service_account() {
    local email=$1 display_name=$2
    if gcloud iam service-accounts describe "$email" --project="$PROJECT_ID" &>/dev/null; then
        print_info "$email already exists"
    else
        gcloud iam service-accounts create "${email%%@*}" \
            --display-name="$display_name" --project="$PROJECT_ID"
        print_success "$email created"
    fi
}

# A service account can take a minute after it is created before IAM accepts it in a binding
grant_project_role() {
    local email=$1 role=$2 attempt
    for attempt in 1 2 3 4 5 6 7 8; do
        if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
             --member="serviceAccount:${email}" --role="$role" --condition=None &>/dev/null; then
            return 0
        fi
        print_info "Waiting for IAM to accept $email ($role)..."
        sleep 10
    done
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${email}" --role="$role" --condition=None >/dev/null
}

setup_service_accounts() {
    print_step 3 "Setting up service accounts"
    local role

    # The service's API container: database, secrets, the bucket (step 4) and starting the worker jobs (step 8)
    ensure_service_account "$API_SA_EMAIL" "MMM Studio API"
    for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter; do
        grant_project_role "$API_SA_EMAIL" "$role"
    done

    ensure_service_account "$WORKER_SA_EMAIL" "MMM Studio workers"
    for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter \
                roles/monitoring.metricWriter; do
        grant_project_role "$WORKER_SA_EMAIL" "$role"
    done

    # Cloud Build runs as this account: newer projects give the default build account no roles
    ensure_service_account "$BUILD_SA_EMAIL" "MMM Studio image builds"
    grant_project_role "$BUILD_SA_EMAIL" roles/run.builder
    print_success "IAM roles granted"
}

# ── Step 4: Artifact bucket ───────────────────────────────────────────────────
setup_bucket() {
    print_step 4 "Artifact bucket (gs://$BUCKET_NAME)"
    if gcloud storage buckets describe "gs://$BUCKET_NAME" --project="$PROJECT_ID" &>/dev/null; then
        print_info "Bucket already exists"
    else
        gcloud storage buckets create "gs://$BUCKET_NAME" \
            --project="$PROJECT_ID" \
            --location="$REGION" \
            --uniform-bucket-level-access \
            --public-access-prevention
    fi
    local email
    for email in "$API_SA_EMAIL" "$WORKER_SA_EMAIL"; do
        gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
            --member="serviceAccount:${email}" --role=roles/storage.objectAdmin >/dev/null
    done
    print_success "Bucket ready; both service accounts are object admins on it only"
}

# ── Step 5: Cloud SQL and secrets ─────────────────────────────────────────────
setup_database() {
    print_step 5 "Cloud SQL ($SQL_INSTANCE) and secrets"

    if gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" &>/dev/null; then
        print_info "Instance already exists"
    else
        print_info "Creating a PostgreSQL 16 instance, $SQL_TIER (10–15 min)..."
        # db-f1-micro is an Enterprise-edition tier; PostgreSQL 16 defaults to Enterprise Plus
        gcloud sql instances create "$SQL_INSTANCE" \
            --project="$PROJECT_ID" \
            --region="$REGION" \
            --database-version=POSTGRES_16 \
            --edition=ENTERPRISE \
            --tier="$SQL_TIER" \
            --storage-type=SSD \
            --storage-size=10 \
            --availability-type=zonal \
            --backup-start-time=02:00
    fi
    SQL_CONNECTION=$(sql_connection)

    if ! gcloud sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE" \
         --project="$PROJECT_ID" &>/dev/null; then
        gcloud sql databases create "$DB_NAME" --instance="$SQL_INSTANCE" --project="$PROJECT_ID"
    fi

    # The password lives only in the database-URL secret; keep it so running revisions stay connected.
    local stored_url password generated=0 users url
    stored_url=$(read_secret "$DB_URL_SECRET")
    password=$(printf '%s' "$stored_url" | sed -n -E "s|^postgresql://${DB_USER}:([^@]+)@.*|\\1|p")
    if [ -z "$password" ]; then
        password=$(openssl rand -hex 24)
        generated=1
    fi
    users=$(gcloud sql users list --instance="$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(name)')
    if echo "$users" | grep -qx "$DB_USER"; then
        if [ "$generated" == 1 ]; then
            gcloud sql users set-password "$DB_USER" --instance="$SQL_INSTANCE" \
                --password="$password" --project="$PROJECT_ID"
        fi
    else
        gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" \
            --password="$password" --project="$PROJECT_ID"
    fi
    url="postgresql://${DB_USER}:${password}@/${DB_NAME}?host=/cloudsql/${SQL_CONNECTION}"
    if [ "$url" != "$stored_url" ]; then
        printf '%s' "$url" | write_secret "$DB_URL_SECRET"
        print_info "Stored the database URL in Secret Manager ($DB_URL_SECRET)"
    fi

    if [ -n "${APP_ACCESS_CODE:-}" ] && [ "$APP_ACCESS_CODE" != "$(read_secret "$ACCESS_CODE_SECRET")" ]; then
        printf '%s' "$APP_ACCESS_CODE" | write_secret "$ACCESS_CODE_SECRET"
        print_info "Stored the access code in Secret Manager ($ACCESS_CODE_SECRET)"
    fi
    print_success "Database $DB_NAME ready on $SQL_CONNECTION"
}

# ── Step 6: Images (Cloud Build) ──────────────────────────────────────────────
# Files listed in src/app/.gcloudignore (virtualenvs, node_modules, .env, local run data) are never uploaded.
build_images() {
    print_step 6 "Building images with Cloud Build"

    if ! gcloud artifacts repositories describe "$AR_REPOSITORY" \
         --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        gcloud artifacts repositories create "$AR_REPOSITORY" \
            --repository-format=docker --location="$REGION" --project="$PROJECT_ID" \
            --description="MMM Studio container images"
    fi

    local registry tmpconfig
    registry="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}"
    API_IMAGE="${registry}/api:${STAMP}"
    WORKER_IMAGE="${registry}/worker:${STAMP}"
    WEB_IMAGE="${registry}/web:${STAMP}"

    # The worker build reuses the api build's base layers; the web image builds alongside them
    tmpconfig=$(mktemp "${TMPDIR:-/tmp}/mmm-studio-cloudbuild-XXXXXX")
    cat > "$tmpconfig" <<EOF
steps:
  - id: api
    name: 'gcr.io/cloud-builders/docker'
    args: ['build', '--target', 'api', '-t', '${API_IMAGE}', 'backend']
  - id: worker
    name: 'gcr.io/cloud-builders/docker'
    args: ['build', '--target', 'worker', '-t', '${WORKER_IMAGE}', 'backend']
    waitFor: ['api']
  - id: web
    name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', 'frontend/Dockerfile', '-t', '${WEB_IMAGE}', '.']
    waitFor: ['-']
images:
  - '${API_IMAGE}'
  - '${WORKER_IMAGE}'
  - '${WEB_IMAGE}'
timeout: 3600s
options:
  machineType: E2_HIGHCPU_8
  logging: CLOUD_LOGGING_ONLY
EOF
    print_info "Submitting to Cloud Build → $registry (tag $STAMP; the worker image with TensorFlow takes a while)"
    gcloud builds submit "$BUILD_CONTEXT" \
        --project="$PROJECT_ID" \
        --config="$tmpconfig" \
        --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA_EMAIL}"
    rm -f "$tmpconfig"
    print_success "Images pushed"
}

# ── Step 7: Worker jobs ───────────────────────────────────────────────────────
deploy_jobs() {
    print_step 7 "Deploying the worker jobs"
    local name queue cpu memory timeout flags
    local env="MMM_STORAGE_BACKEND=gcs,MMM_GCS_BUCKET=${BUCKET_NAME},MMM_GCP_PROJECT_ID=${PROJECT_ID},MMM_GCP_REGION=${REGION},MMM_GIT_SHA=${GIT_SHA}"

    echo "$WORKER_JOBS" | while read -r name queue cpu memory timeout flags; do
        [ -z "$name" ] && continue
        gcloud run jobs deploy "$name" \
            --project="$PROJECT_ID" \
            --region="$REGION" \
            --image="$WORKER_IMAGE" \
            --service-account="$WORKER_SA_EMAIL" \
            --set-cloudsql-instances="$SQL_CONNECTION" \
            --set-env-vars="$env" \
            --set-secrets="MMM_DATABASE_URL=${DB_URL_SECRET}:latest" \
            --args="worker,--queue,${queue},${flags}" \
            --cpu="$cpu" --memory="$memory" \
            --task-timeout="$timeout" \
            --tasks=1 --max-retries=0
        # The API starts executions of this job (run.jobs.run) when it defers work on its queue
        gcloud run jobs add-iam-policy-binding "$name" \
            --project="$PROJECT_ID" --region="$REGION" \
            --member="serviceAccount:${API_SA_EMAIL}" --role=roles/run.invoker >/dev/null
        print_success "$name ($queue: $cpu vCPU / $memory, timeout $timeout)"
    done
}

# ── Step 8: Service ───────────────────────────────────────────────────────────
deploy_service() {
    print_step 8 "Deploying the Cloud Run service ($SERVICE_NAME)"

    local access_code_env="" manifest
    if secret_exists "$ACCESS_CODE_SECRET"; then
        access_code_env="
            - name: MMM_APP_ACCESS_CODE
              valueFrom:
                secretKeyRef:
                  name: ${ACCESS_CODE_SECRET}
                  key: latest"
        print_info "Actions that cost money need the access code ($ACCESS_CODE_SECRET)"
    else
        print_warning "No access code: anyone with the URL can upload workbooks and start Meridian fits"
    fi

    manifest=$(mktemp "${TMPDIR:-/tmp}/mmm-studio-service-XXXXXX")
    cat > "$manifest" <<EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${SERVICE_NAME}
  labels:
    cloud.googleapis.com/location: ${REGION}
  annotations:
    run.googleapis.com/ingress: all
    # Public without an allUsers binding, so it also works under domain-restricted sharing
    run.googleapis.com/invoker-iam-disabled: 'true'
spec:
  template:
    metadata:
      # A new name on every deploy rolls out a revision, which reads the latest secret versions
      name: ${SERVICE_NAME}-${STAMP}
      annotations:
        autoscaling.knative.dev/maxScale: '2'
        run.googleapis.com/cloudsql-instances: ${SQL_CONNECTION}
        run.googleapis.com/container-dependencies: '{"web":["api"]}'
        run.googleapis.com/startup-cpu-boost: 'true'
    spec:
      serviceAccountName: ${API_SA_EMAIL}
      timeoutSeconds: 300
      containerConcurrency: 40
      containers:
        - name: web
          image: ${WEB_IMAGE}
          ports:
            - name: http1
              containerPort: 8080
          env:
            - name: API_ORIGIN
              value: http://127.0.0.1:8000
          resources:
            limits:
              cpu: '1'
              memory: 256Mi
        - name: api
          image: ${API_IMAGE}
          command: ["uvicorn"]
          args: ["mmm_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
          env:
            - name: MMM_STORAGE_BACKEND
              value: gcs
            - name: MMM_GCS_BUCKET
              value: ${BUCKET_NAME}
            - name: MMM_GCP_PROJECT_ID
              value: ${PROJECT_ID}
            - name: MMM_GCP_REGION
              value: ${REGION}
            - name: MMM_WAKE_WORKERS
              value: cloudrun
            - name: MMM_GIT_SHA
              value: '${GIT_SHA}'
            - name: MMM_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: ${DB_URL_SECRET}
                  key: latest${access_code_env}
          resources:
            limits:
              cpu: '1'
              memory: 2Gi
          startupProbe:
            httpGet:
              path: /api/v1/health
              port: 8000
            periodSeconds: 5
            timeoutSeconds: 3
            failureThreshold: 24
EOF

    gcloud run services replace "$manifest" --region="$REGION" --project="$PROJECT_ID"
    rm -f "$manifest"
    print_success "Service deployed: $(service_url)"
}

# Images and connection of what is deployed, for the seed subcommand
load_deployed_state() {
    SQL_CONNECTION=$(sql_connection)
    if [ -z "$(service_url)" ]; then
        print_error "Service $SERVICE_NAME not found in $REGION — run the full deploy first"
        exit 1
    fi
}

# ── Step 9: Seed run ──────────────────────────────────────────────────────────
# Uploads the case-study workbook through the API and starts one run, which also smoke-tests the cloud
# path end to end (upload → GCS, both queues, Cloud Run Job wake-up). Skipped when the app has runs.
api_curl() {
    local code
    code=$(read_secret "$ACCESS_CODE_SECRET")
    if [ -n "$code" ]; then
        curl -fsS -H "X-Access-Code: ${code}" "$@"
    else
        curl -fsS "$@"
    fi
}

seed_run() {
    print_step 9 "Seeding a $SEED_PROFILE run"
    local url count run_id
    url=$(service_url)
    count=$(curl -fsS "$url/api/v1/runs" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["runs"]))')
    if [ "$count" != "0" ]; then
        print_info "The app already has $count run(s) — skipping the seed"
        return 0
    fi
    if [ ! -f "$SEED_WORKBOOK" ]; then
        print_warning "Workbook not found ($SEED_WORKBOOK) — skipping the seed"
        return 0
    fi
    run_id=$(api_curl -X POST "$url/api/v1/runs" \
        -F "file=@${SEED_WORKBOOK}" -F "name=Case study workbook" -F "profile=${SEED_PROFILE}" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
    api_curl -X POST "$url/api/v1/runs/${run_id}/start" \
        -H "Content-Type: application/json" -d "{\"profile\": \"${SEED_PROFILE}\", \"seed\": 0}" >/dev/null
    print_success "Started run $run_id ($SEED_PROFILE profile)"
    echo "  Follow it in the app, or with:"
    echo "  gcloud run jobs executions list --job=mmm-studio-worker-meridian --region=$REGION --project=$PROJECT_ID"
}

# ── Summary ───────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  App URL: $(service_url)${NC}"
    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  Project:   $PROJECT_ID ($REGION), commit $GIT_SHA"
    echo "  Database:  $SQL_CONNECTION ($SQL_TIER, the only resource billed while idle)"
    echo "  Artifacts: gs://$BUCKET_NAME"
    echo ""
    echo -e "${YELLOW}Useful commands:${NC}"
    echo "  gcloud run services logs read $SERVICE_NAME --region=$REGION --project=$PROJECT_ID"
    echo "  gcloud logging read 'resource.type=\"cloud_run_job\"' --project=$PROJECT_ID --limit=50"
    echo "  gcloud sql instances patch $SQL_INSTANCE --activation-policy=NEVER --project=$PROJECT_ID   # pause the DB"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    set -euo pipefail
    local command="${1:-all}"

    case "$command" in
        all|update|seed) ;;
        *) print_error "Unknown command: $command (all | update | seed)"; exit 1 ;;
    esac

    echo ""
    echo -e "${CYAN}MMM Studio — Cloud Run deployment ($command)${NC}"
    echo "  Project:   ${PROJECT_ID:-<not set>}"
    echo "  Region:    $REGION"
    echo "  Service:   $SERVICE_NAME + jobs mmm-studio-worker-{analysis,meridian,interactive}"
    echo "  Cloud SQL: $SQL_INSTANCE ($SQL_TIER)"
    echo ""
    if [ "${ASSUME_YES:-0}" != "1" ]; then
        read -rp "Continue? (y/n) " -n 1; echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && echo "Aborted." && exit 0
    fi

    check_prerequisites
    case "$command" in
        all)
            enable_apis
            setup_service_accounts
            setup_bucket
            setup_database
            build_images
            deploy_jobs
            deploy_service
            seed_run
            ;;
        update)
            SQL_CONNECTION=$(sql_connection)
            build_images
            deploy_jobs
            deploy_service
            ;;
        seed)
            load_deployed_state
            seed_run
            ;;
    esac
    print_summary
}

main "$@"
