#!/bin/bash
# Terraform deploy orchestrator.
#
# Usage:
#   ./infra/scripts/deploy.sh [plan|apply|destroy|init] [-target=...]
#
# - Auto-bootstraps the S3 state bucket on first run (S3 native locking).
# - Derives backend config from PROJECT_NAME/AWS_REGION/AWS account id.
# - Re-inits terraform if backend config changed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_DIR="$INFRA_DIR/environments/dev"
BOOTSTRAP_DIR="$INFRA_DIR/bootstrap"

PROJECT_NAME="${PROJECT_NAME:-strands-agent-chatbot}"

ACTION="${1:-apply}"
shift || true

# ------------------------------------------------------------
# Interactive region selector (skipped if AWS_REGION is set)
# ------------------------------------------------------------
select_region() {
  # Non-interactive override: when NON_INTERACTIVE=1 and AWS_REGION is set,
  # skip the picker entirely.
  if [ "${NON_INTERACTIVE:-0}" = "1" ] && [ -n "${AWS_REGION:-}" ]; then
    echo ">>> Using AWS_REGION=$AWS_REGION (non-interactive)"
    return
  fi

  # Auto-detect region from existing Terraform state.
  if [ -z "${AWS_REGION:-}" ] && [ -d "$ENV_DIR/.terraform" ]; then
    local detected
    detected=$(cd "$ENV_DIR" && terraform output -raw aws_region 2>/dev/null || true)
    if [ -n "$detected" ]; then
      AWS_REGION="$detected"
      echo ">>> Detected AWS_REGION=$AWS_REGION from existing deployment"
      return
    fi
  fi

  echo ""
  if [ -n "${AWS_REGION:-}" ]; then
    echo "Current AWS_REGION=$AWS_REGION (press Enter to keep, or choose below to change)"
  fi
  echo "Select AWS Region:"
  echo ""
  echo "  1) us-east-1      (US East - N. Virginia)"
  echo "  2) us-west-2      (US West - Oregon)               [default]"
  echo "  3) ap-northeast-1 (Asia Pacific - Tokyo)"
  echo "  4) ap-northeast-2 (Asia Pacific - Seoul)"
  echo "  5) ap-southeast-1 (Asia Pacific - Singapore)"
  echo "  6) eu-west-1      (Europe - Ireland)"
  echo "  7) eu-central-1   (Europe - Frankfurt)"
  echo "  8) Custom region"
  echo ""
  read -rp "Select region (1-8) [Enter to keep current]: " choice
  if [ -z "$choice" ] && [ -n "${AWS_REGION:-}" ]; then
    :
  else
    case "${choice:-2}" in
      1) AWS_REGION="us-east-1" ;;
      2) AWS_REGION="us-west-2" ;;
      3) AWS_REGION="ap-northeast-1" ;;
      4) AWS_REGION="ap-northeast-2" ;;
      5) AWS_REGION="ap-southeast-1" ;;
      6) AWS_REGION="eu-west-1" ;;
      7) AWS_REGION="eu-central-1" ;;
      8) read -rp "Enter region code: " AWS_REGION ;;
      *) echo "Invalid choice"; exit 1 ;;
    esac
  fi
  echo ""
  echo ">>> Using AWS_REGION=$AWS_REGION"
}

select_region
export AWS_REGION
# AWS SDKs read AWS_DEFAULT_REGION with higher precedence than provider-level
# region in some paths; pin both to avoid cross-region signing errors.
export AWS_DEFAULT_REGION="$AWS_REGION"
export TF_VAR_aws_region="$AWS_REGION"

# State bucket was created in us-east-1 during the first bootstrap run.
# Region of the state bucket is independent of where resources get deployed,
# so we pin it here to avoid accidental bucket renames on region changes.
STATE_REGION="us-east-1"

if ! aws sts get-caller-identity --query Account --output text >/dev/null 2>&1; then
  echo "ERROR: Cannot reach AWS APIs. Check your network connection and credentials."
  exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STATE_BUCKET="${PROJECT_NAME}-tfstate-${ACCOUNT_ID}-${STATE_REGION}"

# ------------------------------------------------------------
# API key prompts (only for apply; skipped if secret already exists)
# Secrets live in AWS Secrets Manager. When a secret is absent, the
# corresponding Lambda tool + Gateway target is automatically excluded
# (see SKIP_* exports below, consumed as Terraform -var flags).
# ------------------------------------------------------------
SKIP_TAVILY=false
SKIP_GOOGLE_SEARCH=false
SKIP_GOOGLE_MAPS=false
ENABLE_MANTLE_MODELS=false

_secret_exists() {
  aws secretsmanager describe-secret --secret-id "$1" --region "$AWS_REGION" >/dev/null 2>&1
}

_ensure_secret() {
  local name="$1" desc="$2" value="$3"
  aws secretsmanager create-secret \
    --name "$name" --secret-string "$value" --description "$desc" \
    --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws secretsmanager put-secret-value \
       --secret-id "$name" --secret-string "$value" \
       --region "$AWS_REGION" >/dev/null 2>&1
}

prompt_api_keys() {
  local mantle_secret="${PROJECT_NAME}/bedrock/api-key"

  # Plan must resolve optional resources exactly as apply does. Existing
  # secrets enable their integrations; absent secrets disable them without
  # prompting during non-interactive actions.
  if [ "$ACTION" != "apply" ]; then
    _secret_exists "${PROJECT_NAME}/mcp/tavily-api-key" || SKIP_TAVILY=true
    _secret_exists "${PROJECT_NAME}/mcp/google-credentials" || SKIP_GOOGLE_SEARCH=true
    _secret_exists "${PROJECT_NAME}/mcp/google-maps-credentials" || SKIP_GOOGLE_MAPS=true
    _secret_exists "$mantle_secret" && ENABLE_MANTLE_MODELS=true
    return 0
  fi

  echo ""
  echo "============================================"
  echo "  Optional API keys (press Enter to skip)"
  echo "  Skipping disables the matching tool."
  echo "============================================"

  # Tavily
  if _secret_exists "${PROJECT_NAME}/mcp/tavily-api-key"; then
    echo ""
    echo "Tavily          : already configured"
  else
    echo ""
    echo "Tavily (AI web search)  https://tavily.com/"
    read -rp "  Tavily API Key: " key
    if [ -n "${key:-}" ]; then
      _ensure_secret "${PROJECT_NAME}/mcp/tavily-api-key" "Tavily API key" "$key"
      echo "  -> stored"
    else
      SKIP_TAVILY=true
      echo "  (skipped)"
    fi
  fi

  # Google Custom Search
  if _secret_exists "${PROJECT_NAME}/mcp/google-credentials"; then
    echo ""
    echo "Google Search   : already configured"
  else
    echo ""
    echo "Google Custom Search  https://developers.google.com/custom-search/v1/overview"
    read -rp "  Google API Key: " key
    if [ -n "${key:-}" ]; then
      read -rp "  Google Search Engine ID (cx): " cx
      if [ -n "${cx:-}" ]; then
        _ensure_secret "${PROJECT_NAME}/mcp/google-credentials" \
          "Google Custom Search credentials" \
          "{\"api_key\":\"$key\",\"search_engine_id\":\"$cx\"}"
        echo "  -> stored"
      else
        SKIP_GOOGLE_SEARCH=true
        echo "  (skipped — engine ID required)"
      fi
    else
      SKIP_GOOGLE_SEARCH=true
      echo "  (skipped)"
    fi
  fi

  # Google Maps
  if _secret_exists "${PROJECT_NAME}/mcp/google-maps-credentials"; then
    echo ""
    echo "Google Maps     : already configured"
  else
    echo ""
    echo "Google Maps Platform  https://console.cloud.google.com/google/maps-apis"
    echo "  Enable: Places API, Directions API, Geocoding API"
    read -rp "  Google Maps API Key: " key
    if [ -n "${key:-}" ]; then
      _ensure_secret "${PROJECT_NAME}/mcp/google-maps-credentials" \
        "Google Maps API key" "{\"api_key\":\"$key\"}"
      echo "  -> stored"
    else
      SKIP_GOOGLE_MAPS=true
      echo "  (skipped)"
    fi
  fi

  # Bedrock API key (for Mantle OpenAI-compatible models: gpt-5.x, grok, gemma-4)
  if _secret_exists "$mantle_secret"; then
    ENABLE_MANTLE_MODELS=true
    echo ""
    echo "Bedrock Mantle  : already configured (gpt-5.x, grok, gemma-4 enabled)"
  else
    echo ""
    echo "Bedrock API Key (enables Mantle models: GPT-5.5, Grok-4.3, Gemma-4)"
    echo "  Generate at: https://console.aws.amazon.com/bedrock/home#/api-keys"
    read -rp "  Bedrock API Key (Enter to skip): " key
    if [ -n "${key:-}" ]; then
      _ensure_secret "$mantle_secret" "Bedrock API key for Mantle OpenAI-compatible models" "$key"
      ENABLE_MANTLE_MODELS=true
      echo "  -> stored"
    else
      echo "  (skipped — GPT-5.5, Grok, Gemma-4 will not be available)"
    fi
  fi

  # Google Maps Embed (frontend key — separate from server-side credentials).
  # The Embed API requires the key in the browser, so it MUST be a distinct key
  # restricted to Maps Embed API + your CloudFront domain in Google Cloud Console.
  if _secret_exists "${PROJECT_NAME}/frontend/google-maps-embed-key"; then
    echo ""
    echo "Google Maps Embed: already configured"
  else
    echo ""
    echo "Google Maps Embed API Key (frontend — exposed in browser bundle)"
    echo "  Create a SEPARATE key in Google Cloud Console, restricted to:"
    echo "    - API: Maps Embed API only"
    echo "    - HTTP referrer: your CloudFront domain"
    read -rp "  Google Maps Embed API Key (Enter to skip): " key
    if [ -n "${key:-}" ]; then
      _ensure_secret "${PROJECT_NAME}/frontend/google-maps-embed-key" \
        "Google Maps Embed API key (frontend-exposed)" "$key"
      echo "  -> stored"
    else
      echo "  (skipped — map will render placeholder)"
    fi
  fi
  echo ""
}

prompt_api_keys
export TF_VAR_enable_mantle_models="$ENABLE_MANTLE_MODELS"

# ------------------------------------------------------------
# OAuth credential prompts (Google / GitHub / Notion)
# Stored as JSON secret per provider; terraform reads them back and creates
# aws_bedrockagentcore_oauth2_credential_provider resources. Skipping a
# provider simply skips its resource.
# ------------------------------------------------------------
GOOGLE_OAUTH_CLIENT_ID=""
GOOGLE_OAUTH_CLIENT_SECRET=""
GITHUB_OAUTH_CLIENT_ID=""
GITHUB_OAUTH_CLIENT_SECRET=""
NOTION_OAUTH_CLIENT_ID=""
NOTION_OAUTH_CLIENT_SECRET=""

_load_oauth_secret() {
  local secret_name="$1" id_var="$2" secret_var="$3"
  local json
  json=$(aws secretsmanager get-secret-value \
    --secret-id "$secret_name" --region "$AWS_REGION" \
    --query SecretString --output text 2>/dev/null || echo "")
  if [ -n "$json" ]; then
    local cid csec
    cid=$(echo "$json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_id',''))" 2>/dev/null || echo "")
    csec=$(echo "$json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_secret',''))" 2>/dev/null || echo "")
    eval "$id_var=\"\$cid\""
    eval "$secret_var=\"\$csec\""
  fi
}

prompt_oauth_providers() {
  # Always load existing secrets so plan/apply both get correct TF_VAR values.
  # Interactive prompts (for brand-new providers) only run on apply.
  local gname="${PROJECT_NAME}/mcp/google-oauth"
  local hname="${PROJECT_NAME}/mcp/github-oauth"
  local nname="${PROJECT_NAME}/mcp/notion-oauth"
  _secret_exists "$gname" && _load_oauth_secret "$gname" GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET
  _secret_exists "$hname" && _load_oauth_secret "$hname" GITHUB_OAUTH_CLIENT_ID GITHUB_OAUTH_CLIENT_SECRET
  _secret_exists "$nname" && _load_oauth_secret "$nname" NOTION_OAUTH_CLIENT_ID NOTION_OAUTH_CLIENT_SECRET

  [ "$ACTION" != "apply" ] && return 0

  echo ""
  echo "============================================"
  echo "  Optional OAuth providers (press Enter to skip)"
  echo "  Skipping disables the matching 3LO provider."
  echo "============================================"

  if _secret_exists "$gname"; then
    echo ""
    echo "Google OAuth   : already configured"
  else
    echo ""
    echo "Google OAuth (Gmail/Calendar 3LO)"
    echo "  https://console.cloud.google.com/apis/credentials"
    read -rp "  Google OAuth Client ID: " cid
    if [ -n "${cid:-}" ]; then
      read -rsp "  Google OAuth Client Secret: " csec; echo ""
      if [ -n "${csec:-}" ]; then
        _ensure_secret "$gname" "Google OAuth (3LO)" \
          "{\"client_id\":\"$cid\",\"client_secret\":\"$csec\"}"
        GOOGLE_OAUTH_CLIENT_ID="$cid"
        GOOGLE_OAUTH_CLIENT_SECRET="$csec"
        echo "  -> stored"
      fi
    else
      echo "  (skipped)"
    fi
  fi

  if _secret_exists "$hname"; then
    echo ""
    echo "GitHub OAuth   : already configured"
  else
    echo ""
    echo "GitHub OAuth"
    echo "  https://github.com/settings/developers"
    read -rp "  GitHub OAuth Client ID: " cid
    if [ -n "${cid:-}" ]; then
      read -rsp "  GitHub OAuth Client Secret: " csec; echo ""
      if [ -n "${csec:-}" ]; then
        _ensure_secret "$hname" "GitHub OAuth (3LO)" \
          "{\"client_id\":\"$cid\",\"client_secret\":\"$csec\"}"
        GITHUB_OAUTH_CLIENT_ID="$cid"
        GITHUB_OAUTH_CLIENT_SECRET="$csec"
        echo "  -> stored"
      fi
    else
      echo "  (skipped)"
    fi
  fi

  if _secret_exists "$nname"; then
    echo ""
    echo "Notion OAuth   : already configured"
  else
    echo ""
    echo "Notion OAuth (Public integration)"
    echo "  https://www.notion.so/my-integrations"
    read -rp "  Notion OAuth Client ID: " cid
    if [ -n "${cid:-}" ]; then
      read -rsp "  Notion OAuth Client Secret: " csec; echo ""
      if [ -n "${csec:-}" ]; then
        _ensure_secret "$nname" "Notion OAuth (3LO)" \
          "{\"client_id\":\"$cid\",\"client_secret\":\"$csec\"}"
        NOTION_OAUTH_CLIENT_ID="$cid"
        NOTION_OAUTH_CLIENT_SECRET="$csec"
        echo "  -> stored"
      fi
    else
      echo "  (skipped)"
    fi
  fi
  echo ""
}

prompt_oauth_providers
export TF_VAR_google_oauth_client_id="$GOOGLE_OAUTH_CLIENT_ID"
export TF_VAR_google_oauth_client_secret="$GOOGLE_OAUTH_CLIENT_SECRET"
export TF_VAR_github_oauth_client_id="$GITHUB_OAUTH_CLIENT_ID"
export TF_VAR_github_oauth_client_secret="$GITHUB_OAUTH_CLIENT_SECRET"
export TF_VAR_notion_oauth_client_id="$NOTION_OAUTH_CLIENT_ID"
export TF_VAR_notion_oauth_client_secret="$NOTION_OAUTH_CLIENT_SECRET"

# ------------------------------------------------------------
# Channel adapters (Telegram, WhatsApp)
# Token stored in Secrets Manager; enable_telegram flag drives deployment.
# ------------------------------------------------------------
ENABLE_TELEGRAM=false
TELEGRAM_BOT_TOKEN=""

prompt_channels() {
  # Always load existing token so plan/apply both get TF_VAR_telegram_bot_token
  # and TF_VAR_enable_telegram correctly.
  local secret_name="${PROJECT_NAME}/telegram/bot-token"
  if _secret_exists "$secret_name"; then
    TELEGRAM_BOT_TOKEN=$(aws secretsmanager get-secret-value \
      --secret-id "$secret_name" --region "$AWS_REGION" \
      --query SecretString --output text 2>/dev/null || echo "")
    if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
      ENABLE_TELEGRAM=true
    fi
  fi

  [ "$ACTION" != "apply" ] && return 0

  echo ""
  echo "============================================"
  echo "  Channel adapters (press Enter to skip)"
  echo "============================================"

  if _secret_exists "$secret_name"; then
    echo ""
    echo "Telegram bot    : already configured"
  else
    echo ""
    echo "Telegram Bot  https://t.me/BotFather"
    read -rp "  Bot Token (from BotFather): " token
    if [ -n "${token:-}" ]; then
      _ensure_secret "$secret_name" "Telegram Bot API token" "$token"
      TELEGRAM_BOT_TOKEN="$token"
      ENABLE_TELEGRAM=true
      echo "  -> stored"
    else
      echo "  (skipped — Telegram adapter will not be deployed)"
    fi
  fi
  echo ""
}

prompt_channels
export TF_VAR_enable_telegram="$ENABLE_TELEGRAM"
export TF_VAR_telegram_bot_token="$TELEGRAM_BOT_TOKEN"

# ------------------------------------------------------------
# Nova Act Workflow (browser automation)
# ------------------------------------------------------------
# Resolved after the shared deploy venv is set up below.
NOVA_ACT_WORKFLOW_NAME="${NOVA_ACT_WORKFLOW_NAME:-}"

# ------------------------------------------------------------
# Bootstrap state backend (one-time)
# ------------------------------------------------------------
# ------------------------------------------------------------
# Registry Lambda build (bundles boto3 because the service is new and the
# Lambda Python runtime boto3 may lag). Rebuilds only when index.py changes.
# ------------------------------------------------------------
# Shared isolated venv for deploy-time Python work (Nova Act + Lambda build).
# Avoids conflicts with the user's global site-packages (nova-act, google-adk, ...).
DEPLOY_VENV="$INFRA_DIR/.deploy-venv"
DEPLOY_PYTHON="${DEPLOY_PYTHON:-$(command -v python3.13 || command -v python3)}"
ensure_deploy_venv() {
  local desired_version current_version
  desired_version="$("$DEPLOY_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  current_version=""
  if [ -x "$DEPLOY_VENV/bin/python" ]; then
    current_version="$("$DEPLOY_VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  fi

  if [ "$current_version" != "$desired_version" ]; then
    echo ">>> Setting up isolated deploy venv (boto3 >= 1.42.89)..."
    rm -rf "$DEPLOY_VENV"
    "$DEPLOY_PYTHON" -m venv "$DEPLOY_VENV"
    "$DEPLOY_VENV/bin/pip" install --quiet --upgrade pip
    "$DEPLOY_VENV/bin/pip" install --quiet "boto3>=1.42.89"
  fi
}

resolve_nova_act_workflow() {
  [ -n "${NOVA_ACT_WORKFLOW_NAME:-}" ] && return 0
  ensure_deploy_venv
  local default_name
  default_name="$(echo "$PROJECT_NAME" | tr '-' '_')_dev_workflow"
  NOVA_ACT_WORKFLOW_NAME="$("$DEPLOY_VENV/bin/python" - "$default_name" <<'PY' 2>/tmp/nova-act-deploy.log || true
import sys, boto3
name = sys.argv[1]
try:
    c = boto3.client("nova-act", region_name="us-east-1")
except Exception as e:
    sys.stderr.write(f"boto3 nova-act client unavailable: {e}\n"); sys.exit(0)
# Response: { workflowDefinitionSummaries: [{ workflowDefinitionName, status, ... }] }
existing = []
try:
    for page in c.get_paginator("list_workflow_definitions").paginate():
        existing.extend(page.get("workflowDefinitionSummaries", []))
except Exception as e:
    sys.stderr.write(f"list_workflow_definitions failed: {e}\n")

# Prefer exact match on our default name; otherwise use the first ACTIVE one.
match = next((w for w in existing if w.get("workflowDefinitionName") == name), None)
if match:
    print(match["workflowDefinitionName"], end=""); sys.exit(0)
active = next((w for w in existing if w.get("status") == "ACTIVE"), None)
if active:
    print(active["workflowDefinitionName"], end=""); sys.exit(0)

try:
    c.create_workflow_definition(name=name)
    print(name, end="")
except c.exceptions.ConflictException:
    # Race or stale list; treat as success.
    print(name, end="")
except Exception as e:
    sys.stderr.write(f"create_workflow_definition failed: {e}\n")
PY
)"
  if [ -n "$NOVA_ACT_WORKFLOW_NAME" ]; then
    echo "Nova Act workflow: $NOVA_ACT_WORKFLOW_NAME"
  else
    echo "WARNING: Could not resolve or create a Nova Act workflow."
    echo "  See /tmp/nova-act-deploy.log for details."
  fi
}

build_registry_lambda() {
  local dir="$INFRA_DIR/modules/registry/lambda"
  local zip="$dir/registry-manager.zip"
  local src="$dir/index.py"
  if [ -f "$zip" ] && [ "$src" -ot "$zip" ]; then
    return 0
  fi
  ensure_deploy_venv
  echo ">>> Building registry-manager.zip..."
  (
    cd "$dir"
    rm -rf build
    mkdir build
    "$DEPLOY_VENV/bin/pip" install --quiet \
      --target build \
      --python-version 3.14 \
      --platform manylinux2014_x86_64 \
      --only-binary=:all: \
      --no-warn-conflicts \
      "boto3>=1.42.89"
    cp index.py build/
    (cd build && zip -qr ../registry-manager.zip .)
    rm -rf build
  )
}

ensure_backend() {
  if aws s3api head-bucket --bucket "$STATE_BUCKET" --region "$STATE_REGION" 2>/dev/null; then
    return 0
  fi

  echo ">>> Bootstrapping Terraform state backend (bucket only; S3 native locking) in $STATE_REGION..."
  (
    cd "$BOOTSTRAP_DIR"
    terraform init -input=false
    terraform apply -auto-approve \
      -var="project_name=${PROJECT_NAME}" \
      -var="aws_region=${STATE_REGION}"
  )
}

# ------------------------------------------------------------
# terraform init with injected backend config
# ------------------------------------------------------------
tf_init() {
  cd "$ENV_DIR"
  terraform init -input=false -reconfigure \
    -backend-config="bucket=${STATE_BUCKET}" \
    -backend-config="region=${STATE_REGION}"
}

tf_init_if_needed() {
  cd "$ENV_DIR"
  if [ ! -d ".terraform" ] || [ ! -f ".terraform/terraform.tfstate" ]; then
    tf_init
    return
  fi
  # Re-run init to pick up newly added modules.
  terraform init -input=false -upgrade=false >/dev/null 2>&1 || tf_init
}

# ------------------------------------------------------------
# Clean up ROLLBACK_COMPLETE CFN stacks so the next apply isn't blocked.
# CloudFormation refuses to update stacks stuck in ROLLBACK_COMPLETE — they
# must be deleted first. Scoped to this project's registry stacks.
# Also deletes any orphan AgentCore registry records from failed creates.
# ------------------------------------------------------------
clean_failed_registry_stacks() {
  cd "$ENV_DIR"
  local prefix="${PROJECT_NAME}-dev-"
  # shellcheck disable=SC2016
  local stacks
  stacks=$(aws cloudformation list-stacks \
    --region "$AWS_REGION" \
    --stack-status-filter ROLLBACK_COMPLETE CREATE_FAILED DELETE_FAILED UPDATE_ROLLBACK_FAILED \
    --query "StackSummaries[?starts_with(StackName, \`${prefix}\`)].StackName" \
    --output text 2>/dev/null || true)
  [ -z "$stacks" ] && return 0
  echo ">>> Cleaning up failed CFN stacks: $stacks"
  for s in $stacks; do
    aws cloudformation delete-stack --stack-name "$s" --region "$AWS_REGION" 2>/dev/null || true
    # Drop the tf state entry so the next apply recreates cleanly.
    case "$s" in
      ${prefix}registry)
        terraform state rm module.registry.aws_cloudformation_stack.registry 2>/dev/null || true
        ;;
      ${prefix}records-mcp)
        terraform state rm 'module.registry.aws_cloudformation_stack.records_mcp[0]' 2>/dev/null || true
        ;;
      ${prefix}records-a2a)
        terraform state rm 'module.registry.aws_cloudformation_stack.records_a2a[0]' 2>/dev/null || true
        ;;
      ${prefix}records-skills)
        terraform state rm 'module.registry.aws_cloudformation_stack.records_skills[0]' 2>/dev/null || true
        ;;
      ${prefix}record-*)
        local key="${s#${prefix}record-}"
        terraform state rm "module.registry.aws_cloudformation_stack.records_skills[\"${key}\"]" 2>/dev/null || true
        terraform state rm "module.registry.aws_cloudformation_stack.records[\"${key}\"]" 2>/dev/null || true
        ;;
    esac
  done
  # Orphan registries (CREATE_FAILED ones the service leaves behind).
  "$DEPLOY_VENV/bin/python" - "$PROJECT_NAME" <<'PY' 2>/dev/null || true
import sys, boto3, os
project = sys.argv[1]
c = boto3.client("bedrock-agentcore-control", region_name=os.environ["AWS_REGION"])
try:
    for r in c.list_registries().get("registries", []):
        if r.get("name", "").startswith(f"{project}-") and "FAILED" in r.get("status", ""):
            try:
                c.delete_registry(registryId=r["registryId"])
                print(f"Deleted orphan registry: {r['registryId']}")
            except Exception as e:
                print(f"Could not delete {r['registryId']}: {e}")
except Exception as e:
    print(f"list_registries failed: {e}")
PY
}

# ------------------------------------------------------------
# Reconcile Registry record descriptor_type drift.
# Registry API rejects UpdateRegistryRecord when descriptor_type changes.
# When a YAML moves between mcp/, a2a/, skills/, the batched stack must be
# deleted and recreated. Delete the affected type stack so terraform recreates.
# ------------------------------------------------------------
reconcile_registry_record_drift() {
  cd "$ENV_DIR"
  local prefix="${PROJECT_NAME}-dev-records-"

  # Check each batched stack for type mismatches
  for type_name in mcp a2a skills; do
    local stack_name="${PROJECT_NAME}-dev-records-${type_name}"
    local status
    status=$(aws cloudformation describe-stacks \
      --stack-name "$stack_name" --region "$AWS_REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")

    if [ "$status" = "DOES_NOT_EXIST" ] || [ "$status" = "DELETE_COMPLETE" ]; then
      continue
    fi

    # If stack is in a failed state, delete it for clean recreation
    case "$status" in
      *FAILED*|ROLLBACK_COMPLETE)
        echo ">>> Registry records-${type_name} in $status, deleting for recreation"
        aws cloudformation delete-stack --stack-name "$stack_name" --region "$AWS_REGION" 2>/dev/null || true
        aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$AWS_REGION" 2>/dev/null || true
        terraform state rm "module.registry.aws_cloudformation_stack.records_${type_name}[0]" 2>/dev/null || true
        ;;
    esac
  done

}

# ------------------------------------------------------------
# Import pre-existing OAuth2 credential providers into state.
# Runs before apply. AgentCore keeps providers account-wide, so a partial
# destroy (or a tool deleting them out-of-band) can leave state empty while
# AWS still holds them — causing "already exists" on the next apply.
# ------------------------------------------------------------
import_orphan_oauth_providers() {
  cd "$ENV_DIR"
  local name addr enabled
  for p in google github notion; do
    case "$p" in
      google) name="google-oauth-provider"; enabled="$GOOGLE_OAUTH_CLIENT_ID" ;;
      github) name="github-oauth-provider"; enabled="$GITHUB_OAUTH_CLIENT_ID" ;;
      notion) name="notion-oauth-provider"; enabled="$NOTION_OAUTH_CLIENT_ID" ;;
    esac
    [ -z "$enabled" ] && continue
    addr="module.oauth_providers.aws_bedrockagentcore_oauth2_credential_provider.${p}[0]"
    # Already tracked — nothing to do.
    if terraform state list 2>/dev/null | grep -qx "$addr"; then
      continue
    fi
    # Best-effort probe: attempt import; ignore failure (resource may not exist).
    if terraform import -var="aws_region=${AWS_REGION}" \
        -var="enable_tavily=$([ "$SKIP_TAVILY" = true ] && echo false || echo true)" \
        -var="enable_google_search=$([ "$SKIP_GOOGLE_SEARCH" = true ] && echo false || echo true)" \
        -var="enable_google_maps=$([ "$SKIP_GOOGLE_MAPS" = true ] && echo false || echo true)" \
        "$addr" "$name" >/dev/null 2>&1; then
      echo ">>> Imported existing OAuth provider: $name"
    fi
  done
}

# ------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------
ensure_backend
ensure_deploy_venv
export DEPLOY_VENV_PYTHON="$DEPLOY_VENV/bin/python"
resolve_nova_act_workflow
export TF_VAR_nova_act_workflow_name="${NOVA_ACT_WORKFLOW_NAME:-}"
build_registry_lambda

# Registry definitions are no longer copied into the container image.
# The app discovers skills and endpoints via the AgentCore Registry API.

case "$ACTION" in
  init)
    tf_init
    ;;
  plan)
    tf_init_if_needed
    cd "$ENV_DIR"
    terraform plan -var="aws_region=${AWS_REGION}" \
      -var="enable_tavily=$([ "$SKIP_TAVILY" = true ] && echo false || echo true)" \
      -var="enable_google_search=$([ "$SKIP_GOOGLE_SEARCH" = true ] && echo false || echo true)" \
      -var="enable_google_maps=$([ "$SKIP_GOOGLE_MAPS" = true ] && echo false || echo true)" \
      "$@"
    ;;
  apply)
    tf_init_if_needed
    import_orphan_oauth_providers
    clean_failed_registry_stacks
    reconcile_registry_record_drift
    cd "$ENV_DIR"
    terraform apply -auto-approve -var="aws_region=${AWS_REGION}" \
      -var="enable_tavily=$([ "$SKIP_TAVILY" = true ] && echo false || echo true)" \
      -var="enable_google_search=$([ "$SKIP_GOOGLE_SEARCH" = true ] && echo false || echo true)" \
      -var="enable_google_maps=$([ "$SKIP_GOOGLE_MAPS" = true ] && echo false || echo true)" \
      "$@"

    echo ""
    echo "========================================"
    echo "  Deployment Complete"
    echo "========================================"
    echo ""
    echo "Key Resources:"
    echo "  CloudFront URL:      $(terraform output -raw chat_cloudfront_url 2>/dev/null || echo 'N/A')"
    echo "  Gateway URL:         $(terraform output -raw gateway_url 2>/dev/null || echo 'N/A')"
    echo "  Orchestrator ARN:    $(terraform output -raw orchestrator_runtime_arn 2>/dev/null || echo 'N/A')"
    echo "  Cognito User Pool:   $(terraform output -raw cognito_user_pool_id 2>/dev/null || echo 'N/A')"
    echo "  Cognito App Client:  $(terraform output -raw cognito_app_client_id 2>/dev/null || echo 'N/A')"
    echo "  Memory ID:           $(terraform output -raw memory_id 2>/dev/null || echo 'N/A')"
    echo ""

    # Print OAuth callback URLs (must be registered in each provider's console)
    has_oauth=false
    for provider in google-oauth-provider github-oauth-provider notion-oauth-provider; do
      cb=$("$DEPLOY_VENV/bin/python" -c "
import boto3
try:
    r = boto3.client('bedrock-agentcore-control', region_name='$AWS_REGION').get_oauth2_credential_provider(name='$provider')
    print(r.get('callbackUrl', ''))
except: pass
" 2>/dev/null)
      if [ -n "$cb" ]; then
        if [ "$has_oauth" = false ]; then
          echo "OAuth Callback URIs (register in each provider's console):"
          has_oauth=true
        fi
        echo "  $provider: $cb"
      fi
    done
    [ "$has_oauth" = true ] && echo ""

    echo "  Run 'cd infra/environments/dev && terraform output' for all outputs."
    echo ""
    ;;
  destroy)
    tf_init_if_needed
    cd "$ENV_DIR"
    terraform destroy -auto-approve -var="aws_region=${AWS_REGION}" \
      -var="enable_tavily=true" \
      -var="enable_google_search=true" \
      -var="enable_google_maps=true" \
      "$@"
    ;;
  *)
    echo "Unknown action: $ACTION (expected init|plan|apply|destroy)"
    exit 1
    ;;
esac
