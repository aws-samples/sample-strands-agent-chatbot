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
  description = "Wire the Bedrock API key secret into the orchestrator so Mantle OpenAI-compatible models (gpt-5.x, grok, gemma-4) can be selected. Requires a Secrets Manager secret at <project_name>/bedrock/api-key."
  type        = bool
  default     = false
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
