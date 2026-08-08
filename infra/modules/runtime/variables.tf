variable "repo_root" {
  description = "Absolute path to the repository root"
  type        = string
}

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

variable "component_name" {
  description = "Component name, e.g. 'mcp-3lo', 'orchestrator', 'code-agent'"
  type        = string
}

variable "source_dir" {
  description = "Source directory relative to repo_root (informational; CodeBuild zips build_context)"
  type        = string
}

variable "dockerfile_path" {
  description = "Dockerfile path relative to the build context"
  type        = string
  default     = "Dockerfile"
}

variable "build_context" {
  description = "Docker build context path relative to repo_root"
  type        = string
}

variable "runtime_type" {
  description = "component | orchestrator | a2a_agent | http_agent | mcp_3lo"
  type        = string
  validation {
    condition     = contains(["component", "orchestrator", "a2a_agent", "http_agent", "mcp_3lo"], var.runtime_type)
    error_message = "runtime_type must be one of: component, orchestrator, a2a_agent, http_agent, mcp_3lo"
  }
}

# Cognito JWT inbound auth
variable "cognito_issuer_url" {
  description = "OIDC issuer URL. Empty string disables inbound JWT auth."
  type        = string
  default     = ""
}

variable "cognito_allowed_clients" {
  description = "Cognito client IDs allowed to call this runtime (audience boundary)."
  type        = list(string)
  default     = []
}

# Data plane (optional; present only for orchestrator/components needing DDB)
variable "user_data_table_arn" {
  type    = string
  default = ""
}

variable "global_data_table_arn" {
  type    = string
  default = ""
}

variable "user_data_table_name" {
  type    = string
  default = ""
}

variable "global_data_table_name" {
  type    = string
  default = ""
}

variable "orchestration_table_arn" {
  type    = string
  default = ""
}

variable "orchestration_table_name" {
  type    = string
  default = ""
}

variable "enable_ddb_policy" {
  description = "Attach DDB access policy to execution role. Set true when user_data_table_arn or global_data_table_arn is wired."
  type        = bool
  default     = false
}

# Orchestrator-only wiring
variable "gateway_url" {
  type    = string
  default = ""
}

variable "registry_id" {
  type    = string
  default = ""
}

variable "memory_id" {
  type    = string
  default = ""
}

# Processing agent artifact bucket
variable "artifact_bucket_arn" {
  type    = string
  default = ""
}

variable "artifact_bucket_name" {
  type    = string
  default = ""
}

variable "read_only_bucket_arns" {
  type    = list(string)
  default = []
}

variable "secret_arns" {
  description = "Secrets Manager secrets that this Runtime may read."
  type        = list(string)
  default     = []
}

# Extra env vars (merged into Runtime environment_variables)
variable "extra_env_vars" {
  type    = map(string)
  default = {}
}

# Network (phase 1: PUBLIC only)
variable "network_mode" {
  type    = string
  default = "PUBLIC"
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}
