data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  root_dir   = abspath("${path.module}/../../..")
}

module "auth" {
  source = "../../modules/auth"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region

  callback_urls = ["http://localhost:3000/api/auth/callback"]

}

module "memory" {
  source = "../../modules/memory"

  project_name = var.project_name
  environment  = var.environment
}

module "data" {
  source = "../../modules/data"

  project_name = var.project_name
  environment  = var.environment
}

# Shared AgentCore resources (CodeInterpreter + Browser) consumed by
# orchestrator and A2A agents via SSM params + SOURCE_HASH env.
module "agentcore_shared" {
  source = "../../modules/agentcore-shared"

  project_name           = var.project_name
  environment            = var.environment
  aws_region             = var.aws_region
  account_id             = local.account_id
  nova_act_workflow_name = var.nova_act_workflow_name
}

# ============================================================
# MCP 3LO Runtime (Phase 1 verification target)
# ============================================================

# ============================================================
# Gateway Lambda tools (7 functions)
# ============================================================

data "aws_secretsmanager_secret" "tavily" {
  count = var.enable_tavily ? 1 : 0
  name  = "${var.project_name}/mcp/tavily-api-key"
}

data "aws_secretsmanager_secret" "google_creds" {
  count = var.enable_google_search ? 1 : 0
  name  = "${var.project_name}/mcp/google-credentials"
}

data "aws_secretsmanager_secret" "google_maps_creds" {
  count = var.enable_google_maps ? 1 : 0
  name  = "${var.project_name}/mcp/google-maps-credentials"
}

data "aws_secretsmanager_secret" "bedrock_api_key" {
  count = var.enable_mantle_models ? 1 : 0
  name  = "${var.project_name}/bedrock/api-key"
}

locals {
  lambda_tools_root = "${local.root_dir}/agentcore/gateway-tools/lambda-functions"

  # Always-on tools (no API key needed).
  _base_tools = {
    wikipedia    = { secrets = [], env = {}, upload_to_s3 = false }
    arxiv        = { secrets = [], env = {}, upload_to_s3 = false }
    finance      = { secrets = [], env = {}, upload_to_s3 = true }
    weather      = { secrets = [], env = {}, upload_to_s3 = false }
    "web-search" = { secrets = [], env = {}, upload_to_s3 = false }
  }

  _tavily_tool = var.enable_tavily ? {
    tavily = {
      secrets      = [data.aws_secretsmanager_secret.tavily[0].arn]
      env          = { TAVILY_API_KEY_SECRET_NAME = data.aws_secretsmanager_secret.tavily[0].name }
      upload_to_s3 = false
    }
  } : {}

  _google_search_tool = var.enable_google_search ? {
    "google-search" = {
      secrets      = [data.aws_secretsmanager_secret.google_creds[0].arn]
      env          = { GOOGLE_CREDENTIALS_SECRET_NAME = data.aws_secretsmanager_secret.google_creds[0].name }
      upload_to_s3 = false
    }
  } : {}

  _google_maps_tool = var.enable_google_maps ? {
    "google-maps" = {
      secrets      = [data.aws_secretsmanager_secret.google_maps_creds[0].arn]
      env          = { GOOGLE_MAPS_CREDENTIALS_SECRET_NAME = data.aws_secretsmanager_secret.google_maps_creds[0].name }
      upload_to_s3 = false
    }
  } : {}

  lambda_tool_config = merge(
    local._base_tools,
    local._tavily_tool,
    local._google_search_tool,
    local._google_maps_tool,
  )
}



# Shared artifact bucket used by code-agent / research-agent / orchestrator / chat.
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.project_name}-${var.environment}-artifacts-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  cors_rule {
    allowed_methods = ["GET", "PUT", "HEAD"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_ssm_parameter" "artifact_bucket" {
  name  = "/${var.project_name}/${var.environment}/agentcore/artifact-bucket"
  type  = "String"
  value = aws_s3_bucket.artifacts.id
}

resource "aws_s3_bucket" "lambda_artifacts" {
  bucket        = "${var.project_name}-${var.environment}-lambda-artifacts-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lambda_artifacts" {
  bucket = aws_s3_bucket.lambda_artifacts.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
  }
}

module "gateway_lambda_tools" {
  source   = "../../modules/gateway-lambda-tool"
  for_each = local.lambda_tool_config

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = local.account_id

  tool_name       = each.key
  source_root     = local.lambda_tools_root
  secret_arns     = each.value.secrets
  env_vars        = each.value.env
  artifact_bucket = aws_s3_bucket.lambda_artifacts.id
  upload_to_s3    = each.value.upload_to_s3
  pip_command     = "${local.root_dir}/infra/.deploy-venv/bin/pip"
}

# ============================================================
# Gateway
# ============================================================

module "gateway" {
  source = "../../modules/gateway"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = local.account_id

  # Gateway is called service-to-service (orchestrator runtime -> gateway MCP),
  # so the M2M client must be in the allowed list. Runtimes themselves stay
  # user-JWT only (app + web clients).
  cognito_issuer_url = module.auth.issuer_url
  cognito_allowed_clients = [
    module.auth.app_client_id,
    module.auth.web_client_id,
    module.auth.m2m_client_id,
  ]

  lambda_tool_arns = {
    for k, m in module.gateway_lambda_tools : k => m.function_arn
  }

  depends_on = [module.auth, module.gateway_lambda_tools]
}

# ============================================================
# A2A Runtimes (code-agent, research-agent)
# ============================================================

module "runtime_code_agent" {
  source = "../../modules/runtime"

  repo_root      = local.root_dir
  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  account_id     = local.account_id
  component_name = "code-agent"
  runtime_type   = "a2a_agent"

  source_dir      = "agentcore/a2a-agents/code-agent"
  build_context   = "agentcore/a2a-agents/code-agent"
  dockerfile_path = "Dockerfile"

  cognito_issuer_url      = module.auth.issuer_url
  cognito_allowed_clients = [module.auth.app_client_id, module.auth.web_client_id, module.auth.m2m_client_id]

  artifact_bucket_arn  = aws_s3_bucket.artifacts.arn
  artifact_bucket_name = aws_s3_bucket.artifacts.id

  extra_env_vars = {
    CLAUDE_CODE_USE_BEDROCK = "1"
    ANTHROPIC_MODEL         = var.code_agent_model_id
    CODE_AGENT_MODEL_ID     = var.code_agent_model_id
  }

  depends_on = [module.auth, aws_s3_bucket.artifacts, module.agentcore_shared]
}

module "runtime_research_agent" {
  source = "../../modules/runtime"

  repo_root      = local.root_dir
  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  account_id     = local.account_id
  component_name = "research-agent"
  runtime_type   = "a2a_agent"

  source_dir      = "agentcore/a2a-agents/research-agent"
  build_context   = "agentcore/a2a-agents/research-agent"
  dockerfile_path = "Dockerfile"

  cognito_issuer_url      = module.auth.issuer_url
  cognito_allowed_clients = [module.auth.app_client_id, module.auth.web_client_id, module.auth.m2m_client_id]

  artifact_bucket_arn  = aws_s3_bucket.artifacts.arn
  artifact_bucket_name = aws_s3_bucket.artifacts.id

  secret_arns = var.enable_mantle_models ? [
    data.aws_secretsmanager_secret.bedrock_api_key[0].arn
  ] : []

  extra_env_vars = merge(
    {
      CODE_INTERPRETER_ID = module.agentcore_shared.code_interpreter_id
      MODEL_ID            = var.research_agent_default_model_id
    },
    var.enable_mantle_models ? {
      BEDROCK_API_KEY_SECRET_NAME = data.aws_secretsmanager_secret.bedrock_api_key[0].name
    } : {},
  )

  depends_on = [module.auth, aws_s3_bucket.artifacts, module.agentcore_shared]
}

# ============================================================
# Orchestrator Runtime (chatbot-app/agentcore — Strands core)
# ============================================================

module "runtime_orchestrator" {
  source = "../../modules/runtime"

  repo_root      = local.root_dir
  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  account_id     = local.account_id
  component_name = "orchestrator"
  runtime_type   = "orchestrator"

  source_dir      = "chatbot-app/agentcore"
  build_context   = "chatbot-app/agentcore"
  dockerfile_path = "Dockerfile"

  cognito_issuer_url      = module.auth.issuer_url
  cognito_allowed_clients = [module.auth.app_client_id, module.auth.web_client_id, module.auth.m2m_client_id]

  gateway_url = module.gateway.gateway_url
  memory_id   = module.memory.memory_id

  enable_ddb_policy        = true
  user_data_table_arn      = module.data.users_table_arn
  user_data_table_name     = module.data.users_table_name
  global_data_table_arn    = ""
  global_data_table_name   = ""
  orchestration_table_arn  = module.data.session_orchestration_table_arn
  orchestration_table_name = module.data.session_orchestration_table_name

  artifact_bucket_arn  = aws_s3_bucket.artifacts.arn
  artifact_bucket_name = aws_s3_bucket.artifacts.id

  extra_env_vars = merge(
    {
      DYNAMODB_USERS_TABLE                  = module.data.users_table_name
      DYNAMODB_SESSIONS_TABLE               = module.data.sessions_table_name
      M2M_CLIENT_ID                         = module.auth.m2m_client_id
      SESSION_MAILBOX_DELIVERY_ENABLED      = "true"
      SESSION_MAILBOX_WRITE_ENABLED         = "true"
      MEMORY_ARN                            = module.memory.memory_arn
      CODE_AGENT_RUNTIME_ARN                = module.runtime_code_agent.runtime_arn
      RESEARCH_AGENT_RUNTIME_ARN            = module.runtime_research_agent.runtime_arn
      MCP_3LO_RUNTIME_ARN                   = module.runtime_mcp_3lo.runtime_arn
      CODE_INTERPRETER_ID                   = module.agentcore_shared.code_interpreter_id
      BROWSER_ID                            = module.agentcore_shared.browser_id
      BROWSER_NAME                          = module.agentcore_shared.browser_name
      NOVA_ACT_WORKFLOW_DEFINITION_NAME     = module.agentcore_shared.nova_act_workflow_name
      NOVA_ACT_REGION                       = "us-east-1"
      AGENT_OBSERVABILITY_ENABLED           = "true"
      OTEL_PYTHON_DISTRO                    = "aws_distro"
      OTEL_PYTHON_CONFIGURATOR              = "aws_configurator"
      OTEL_LOGS_EXPORTER                    = "otlp"
      OTEL_PYTHON_DISABLED_INSTRUMENTATIONS = "urllib3,urllib"
    },
    var.enable_mantle_models ? {
      BEDROCK_API_KEY_SECRET_NAME = data.aws_secretsmanager_secret.bedrock_api_key[0].name
    } : {},
  )

  depends_on = [
    module.auth,
    module.gateway,
    module.memory,
    module.data,
    module.runtime_code_agent,
    module.runtime_research_agent,
    module.runtime_mcp_3lo,
    module.agentcore_shared,
    aws_s3_bucket.artifacts,
  ]
}

module "session_mailbox_dispatcher" {
  source = "../../modules/session-mailbox-dispatcher"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = local.account_id
  source_dir   = "${local.root_dir}/infra/lambda/session-mailbox-dispatcher"

  orchestration_stream_arn = module.data.session_orchestration_stream_arn
  orchestration_table_arn  = module.data.session_orchestration_table_arn
  orchestration_table_name = module.data.session_orchestration_table_name
  agentcore_runtime_url    = module.runtime_orchestrator.runtime_invocation_url
  cognito_domain_url       = module.auth.domain_url
  m2m_client_id            = module.auth.m2m_client_id
  m2m_client_secret        = module.auth.m2m_client_secret

  depends_on = [
    module.auth,
    module.data,
    module.runtime_orchestrator,
  ]
}

# ============================================================
# Chat (ECS Fargate + ALB + CloudFront)
# ============================================================

# BFF (chat) expects specific SSM parameter names. Keep the runtime module's
# /runtimes/<name>/arn for generic discovery, and mirror the BFF-expected paths here.
resource "aws_ssm_parameter" "bff_orchestrator_runtime_arn" {
  name  = "/${var.project_name}/${var.environment}/agentcore/runtime-arn"
  type  = "String"
  value = module.runtime_orchestrator.runtime_arn
}

resource "aws_ssm_parameter" "bff_code_agent_runtime_arn" {
  name  = "/${var.project_name}/${var.environment}/a2a/code-agent-runtime-arn"
  type  = "String"
  value = module.runtime_code_agent.runtime_arn
}

resource "aws_ssm_parameter" "bff_research_agent_runtime_arn" {
  name  = "/${var.project_name}/${var.environment}/a2a/research-agent-runtime-arn"
  type  = "String"
  value = module.runtime_research_agent.runtime_arn
}

resource "aws_ssm_parameter" "bff_mcp_3lo_runtime_arn" {
  name  = "/${var.project_name}/${var.environment}/mcp/mcp-3lo-runtime-arn"
  type  = "String"
  value = module.runtime_mcp_3lo.runtime_arn
}

resource "aws_ssm_parameter" "bff_memory_arn" {
  name  = "/${var.project_name}/${var.environment}/agentcore/memory-arn"
  type  = "String"
  value = module.memory.memory_arn
}

resource "aws_ssm_parameter" "bff_memory_id" {
  name  = "/${var.project_name}/${var.environment}/agentcore/memory-id"
  type  = "String"
  value = module.memory.memory_id
}

# Optional frontend-exposed Google Maps Embed API key.
# Stored in Secrets Manager by deploy.sh prompt. Absent secret = empty string
# (map renders placeholder). Separate from server-side google-maps-credentials
# because the Embed API requires the key to be embedded in the browser bundle.
# List-based lookup so terraform doesn't error when the secret doesn't exist.
data "aws_secretsmanager_secrets" "maps_embed" {
  filter {
    name   = "name"
    values = ["${var.project_name}/frontend/google-maps-embed-key"]
  }
}

data "aws_secretsmanager_secret_version" "maps_embed_key" {
  count     = length(data.aws_secretsmanager_secrets.maps_embed.arns) > 0 ? 1 : 0
  secret_id = tolist(data.aws_secretsmanager_secrets.maps_embed.arns)[0]
}

locals {
  google_maps_embed_api_key = try(data.aws_secretsmanager_secret_version.maps_embed_key[0].secret_string, "")
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

module "chat" {
  source = "../../modules/chat"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = local.account_id
  repo_root    = local.root_dir

  vpc_id     = data.aws_vpc.default.id
  subnet_ids = data.aws_subnets.default.ids

  cognito_user_pool_id        = module.auth.user_pool_id
  cognito_user_pool_client_id = module.auth.web_client_id
  cognito_user_pool_domain    = module.auth.domain

  users_table_name                 = module.data.users_table_name
  users_table_arn                  = module.data.users_table_arn
  sessions_table_name              = module.data.sessions_table_name
  sessions_table_arn               = module.data.sessions_table_arn
  session_orchestration_table_name = module.data.session_orchestration_table_name
  session_orchestration_table_arn  = module.data.session_orchestration_table_arn

  memory_id            = module.memory.memory_id
  gateway_url          = module.gateway.gateway_url
  artifact_bucket_arn  = aws_s3_bucket.artifacts.arn
  artifact_bucket_name = aws_s3_bucket.artifacts.id

  orchestrator_runtime_arn = module.runtime_orchestrator.runtime_arn
  orchestrator_runtime_url = module.runtime_orchestrator.runtime_invocation_url
  bedrock_api_key_secret_arn = (
    var.enable_mantle_models
    ? data.aws_secretsmanager_secret.bedrock_api_key[0].arn
    : ""
  )

  frontend_build_args = {
    NEXT_PUBLIC_GOOGLE_MAPS_EMBED_API_KEY = local.google_maps_embed_api_key
  }

  depends_on = [
    module.auth, module.data, module.memory, module.gateway,
    module.runtime_orchestrator, aws_s3_bucket.artifacts,
  ]
}

# ============================================================
# Observability — CloudWatch Vended Logs + X-Ray Traces
# ============================================================

# X-Ray Transaction Search — resource policy + activation
resource "aws_cloudwatch_log_resource_policy" "xray_transaction_search" {
  policy_name = "${var.project_name}-${var.environment}-xray-transaction-search"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TransactionSearchXRayAccess"
      Effect    = "Allow"
      Principal = { Service = "xray.amazonaws.com" }
      Action    = "logs:PutLogEvents"
      Resource = [
        "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:aws/spans:*",
      ]
    }]
  })
}

resource "aws_xray_trace_segment_destination" "transaction_search" {
  destination = "CloudWatchLogs"

  depends_on = [aws_cloudwatch_log_resource_policy.xray_transaction_search]
}

resource "aws_xray_indexing_rule" "default" {
  name = "Default"

  rule {
    probabilistic {
      desired_sampling_percentage = 100
    }
  }

  depends_on = [aws_cloudwatch_log_resource_policy.xray_transaction_search]
}

module "observability_gateway" {
  source        = "../../modules/observability"
  resource_name = "gateway"
  resource_arn  = module.gateway.gateway_arn
  aws_region    = var.aws_region
  project_name  = var.project_name
  environment   = var.environment

  depends_on = [module.gateway, aws_xray_trace_segment_destination.transaction_search, aws_xray_indexing_rule.default]
}

module "observability_memory" {
  source        = "../../modules/observability"
  resource_name = "memory"
  resource_arn  = module.memory.memory_arn
  aws_region    = var.aws_region
  project_name  = var.project_name
  environment   = var.environment

  depends_on = [module.memory, aws_xray_trace_segment_destination.transaction_search, aws_xray_indexing_rule.default]
}

module "observability_code_interpreter" {
  source        = "../../modules/observability"
  resource_name = "code-interpreter"
  resource_arn  = module.agentcore_shared.code_interpreter_arn
  aws_region    = var.aws_region
  project_name  = var.project_name
  environment   = var.environment

  depends_on = [module.agentcore_shared, aws_xray_trace_segment_destination.transaction_search, aws_xray_indexing_rule.default]
}

module "registry" {
  source = "../../modules/registry"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  repo_root    = local.root_dir

  gateway_url     = module.gateway.gateway_url
  mcp_runtime_url = module.runtime_mcp_3lo.runtime_invocation_url
  a2a_runtime_urls = {
    "code-agent"     = module.runtime_code_agent.runtime_invocation_url
    "research-agent" = module.runtime_research_agent.runtime_invocation_url
  }
  a2a_runtime_role_arns = {
    "code-agent"     = module.runtime_code_agent.execution_role_arn
    "research-agent" = module.runtime_research_agent.execution_role_arn
  }
}

module "oauth_providers" {
  source = "../../modules/oauth-providers"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region

  google_client_id     = var.google_oauth_client_id
  google_client_secret = var.google_oauth_client_secret
  github_client_id     = var.github_oauth_client_id
  github_client_secret = var.github_oauth_client_secret
  notion_client_id     = var.notion_oauth_client_id
  notion_client_secret = var.notion_oauth_client_secret
}

module "runtime_mcp_3lo" {
  source = "../../modules/runtime"

  repo_root      = local.root_dir
  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  account_id     = local.account_id
  component_name = "mcp-3lo"
  runtime_type   = "mcp_3lo"

  source_dir      = "agentcore/mcp-runtime"
  build_context   = "agentcore/mcp-runtime"
  dockerfile_path = "Dockerfile"

  cognito_issuer_url = module.auth.issuer_url
  cognito_allowed_clients = [
    module.auth.app_client_id,
    module.auth.web_client_id,
  ]

  network_mode = var.network_mode == "PUBLIC" ? "PUBLIC" : "VPC"

  extra_env_vars = {
    OTEL_PYTHON_DISABLED_INSTRUMENTATIONS = "boto,botocore"
  }

  depends_on = [module.auth]
}

# ============================================================
# 3LO Workload Identity — OAuth callback URL registration
# ============================================================
# AgentCore creates a workload identity alongside the MCP 3LO runtime. The
# 3LO flow requires our callback page to be registered on it as an
# AllowedResourceOauth2ReturnUrl, otherwise GetResourceOauth2Token rejects
# resourceOauth2ReturnUrl. Managed here (not in the runtime module) because the
# URL comes from the chat module, and importing the service-created identity
# keeps it declarative rather than a local-exec that silently no-ops.

resource "aws_bedrockagentcore_workload_identity" "mcp_3lo" {
  name                                = module.runtime_mcp_3lo.workload_identity_name
  allowed_resource_oauth2_return_urls = ["https://${module.chat.cloudfront_domain_name}/oauth-complete"]

  lifecycle {
    # AgentCore owns this identity's lifecycle: deleting it would break the
    # runtime, and its name is derived from the runtime ID.
    prevent_destroy = true
  }
}

# Store OAuth provider callback URLs in SSM for user reference.
# Users must register these URLs in their OAuth app settings
# (Google Cloud Console, GitHub Developer Settings, Notion Integration).
resource "null_resource" "oauth_provider_callback_urls" {
  triggers = {
    google_arn = module.oauth_providers.google_provider_arn
    github_arn = module.oauth_providers.github_provider_arn
    notion_arn = module.oauth_providers.notion_provider_arn
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      REGION="${var.aws_region}"

      for PROVIDER_NAME in google-oauth-provider github-oauth-provider notion-oauth-provider; do
        CALLBACK=$(python3 -c "
import boto3
try:
    client = boto3.client('bedrock-agentcore-control', region_name='$REGION')
    resp = client.get_oauth2_credential_provider(name='$PROVIDER_NAME')
    print(resp.get('callbackUrl', ''), end='')
except Exception:
    print('', end='')
" 2>/dev/null || echo "")

        if [ -n "$CALLBACK" ]; then
          echo "  $PROVIDER_NAME callback: $CALLBACK"
          echo "  >> Register this URL in your OAuth app redirect URIs"
        fi
      done
    EOT
  }

  depends_on = [module.oauth_providers]
}

# ============================================================
# WhatsApp Adapter (optional — Baileys / WhatsApp Web)
# ============================================================

module "telegram" {
  source = "../../modules/telegram"
  count  = var.enable_telegram ? 1 : 0

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = local.account_id
  repo_root    = local.root_dir

  vpc_id     = data.aws_vpc.default.id
  subnet_ids = data.aws_subnets.default.ids

  runtime_invocation_url = module.runtime_orchestrator.runtime_invocation_url
  cognito_domain_url     = module.auth.domain_url
  m2m_client_id          = module.auth.m2m_client_id
  m2m_client_secret      = module.auth.m2m_client_secret
  telegram_bot_token     = var.telegram_bot_token
  allowed_user_ids       = var.telegram_allowed_user_ids
  owner_user_id          = var.telegram_owner_user_id
  artifact_bucket_arn    = aws_s3_bucket.artifacts.arn

  depends_on = [module.runtime_orchestrator, module.auth]
}
