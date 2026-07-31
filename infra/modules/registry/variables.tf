variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "repo_root" {
  description = "Absolute path to repo root (SKILL.md files live under chatbot-app/agentcore/skills)"
  type        = string
}

variable "enabled_components" {
  description = "Whitelist of component names (YAML basenames) to register. Empty = register all discovered definitions."
  type        = list(string)
  default     = []
}

variable "gateway_url" {
  description = "AgentCore Gateway invocation URL used by public MCP skills."
  type        = string
  default     = ""
}

variable "mcp_runtime_url" {
  description = "Stateful MCP Runtime URL used by user-federated skills."
  type        = string
  default     = ""
}

variable "a2a_runtime_urls" {
  description = "Map of A2A agent name to Runtime invocation URL."
  type        = map(string)
  default     = {}
}

variable "a2a_runtime_role_arns" {
  description = "Map of A2A agent name to IAM role ARN for SigV4 authentication."
  type        = map(string)
  default     = {}
}
