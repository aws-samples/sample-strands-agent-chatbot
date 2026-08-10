variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "repo_root" {
  description = "Absolute path to repo root (frontend source lives under chatbot-app/frontend)"
  type        = string
}

variable "frontend_rel_path" {
  description = "Frontend source path relative to repo root"
  type        = string
  default     = "chatbot-app/frontend"
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

# Cognito
variable "cognito_user_pool_id" {
  type = string
}

variable "cognito_user_pool_client_id" {
  type = string
}

variable "cognito_user_pool_domain" {
  type = string
}

# Downstream services
variable "users_table_name" {
  type = string
}

variable "users_table_arn" {
  type = string
}

variable "sessions_table_name" {
  type = string
}

variable "sessions_table_arn" {
  type = string
}

variable "session_orchestration_table_arn" {
  type = string
}

variable "session_orchestration_table_name" {
  type = string
}

variable "memory_id" {
  type = string
}

variable "gateway_url" {
  type = string
}

variable "artifact_bucket_arn" {
  type = string
}

variable "artifact_bucket_name" {
  type = string
}

variable "workspace_file_system_arn" {
  type    = string
  default = ""
}

variable "workspace_access_point_arn" {
  type    = string
  default = ""
}

variable "orchestrator_runtime_arn" {
  type = string
}

variable "orchestrator_runtime_url" {
  type = string
}

variable "bedrock_api_key_secret_arn" {
  description = "Secrets Manager ARN for the Bedrock Mantle API key."
  type        = string
  default     = ""
}

# Build-time secrets passed as build args (e.g., Google Maps embed key, default keys)
variable "frontend_build_args" {
  description = "Extra NEXT_PUBLIC_* build args (key → value)"
  type        = map(string)
  default     = {}
}

variable "task_cpu" {
  type    = number
  default = 2048
}

variable "task_memory" {
  type    = number
  default = 4096
}
