variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "project_name" {
  type    = string
  default = "strands-agent-chatbot"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "enable_tavily" {
  description = "Deploy Tavily Lambda + Gateway target. Set false to skip when no API key is configured."
  type        = bool
  default     = true
}

variable "enable_google_search" {
  description = "Deploy Google Custom Search Lambda + Gateway target."
  type        = bool
  default     = true
}

variable "enable_google_maps" {
  description = "Deploy Google Maps Lambda + Gateway target."
  type        = bool
  default     = true
}

variable "enable_mantle_models" {
  description = "Wire the Bedrock API key secret into model-calling runtimes. Required for GPT-5.6 through Bedrock Runtime Responses and for Mantle-only Gemma 4. Grok 4.6 continues to use Bedrock Runtime IAM authentication. Requires a Secrets Manager secret at <project_name>/bedrock/api-key."
  type        = bool
  default     = false
}

variable "code_interpreter_supported_az_ids" {
  description = "Stable availability zone IDs supported by AgentCore Code Interpreter VPC mode. Empty uses every subnet in the selected VPC."
  type        = list(string)
  default     = []
}

variable "code_interpreter_private_subnets" {
  description = "Availability zone ID to private subnet CIDR mapping used by Code Interpreter for NAT egress."
  type        = map(string)
  default     = {}
}

variable "code_agent_model_id" {
  description = "Optional Code Agent fallback model override. Empty uses the catalog's code_agent/claude/medium model."
  type        = string
  default     = ""

  validation {
    condition = (
      var.code_agent_model_id == ""
      || can(regex("(^|\\.)anthropic\\.claude-", var.code_agent_model_id))
    )
    error_message = "code_agent_model_id must be empty or a Claude model ID."
  }
}

variable "research_agent_default_model_id" {
  description = "Fallback model for direct Research Agent calls without orchestrator model metadata."
  type        = string
  default     = "us.anthropic.claude-sonnet-5"
}

variable "general_subagent_default_model_id" {
  description = "Optional isolated subagent fallback override. Empty uses the catalog's general_subagent/claude/medium model."
  type        = string
  default     = ""
}

variable "google_oauth_client_id" {
  description = "Google OAuth Client ID for Gmail/Calendar MCP 3LO. Empty disables the provider."
  type        = string
  default     = ""
  sensitive   = true
}

variable "google_oauth_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "github_oauth_client_id" {
  description = "GitHub OAuth Client ID. Empty disables the provider."
  type        = string
  default     = ""
  sensitive   = true
}

variable "github_oauth_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "notion_oauth_client_id" {
  description = "Notion OAuth Client ID. Empty disables the provider."
  type        = string
  default     = ""
  sensitive   = true
}

variable "notion_oauth_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "nova_act_workflow_name" {
  description = "Nova Act Workflow Definition Name. Create via: aws nova-act create-workflow-definition --name <name>"
  type        = string
  default     = ""
}

variable "network_mode" {
  description = "PUBLIC | VPC_CREATE | VPC_EXISTING. Phase 1 supports PUBLIC only."
  type        = string
  default     = "PUBLIC"
  validation {
    condition     = contains(["PUBLIC", "VPC_CREATE", "VPC_EXISTING"], var.network_mode)
    error_message = "network_mode must be PUBLIC, VPC_CREATE, or VPC_EXISTING"
  }
}

variable "enable_telegram" {
  description = "Deploy Telegram bot adapter (ECS Fargate)."
  type        = bool
  default     = false
}

variable "telegram_bot_token" {
  description = "Telegram Bot API token from BotFather."
  type        = string
  default     = ""
  sensitive   = true
}

variable "telegram_allowed_user_ids" {
  description = "Comma-separated Telegram user IDs for allowlist (empty = allow all)."
  type        = string
  default     = ""
}

variable "telegram_owner_user_id" {
  description = "Cognito user ID to link Telegram sessions with web identity."
  type        = string
  default     = ""
}
