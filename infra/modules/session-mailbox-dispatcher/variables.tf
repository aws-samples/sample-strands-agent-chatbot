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

variable "source_dir" {
  type = string
}

variable "orchestration_stream_arn" {
  type = string
}

variable "agentcore_runtime_url" {
  type = string
}

variable "cognito_domain_url" {
  type = string
}

variable "m2m_client_id" {
  type = string
}

variable "m2m_client_secret" {
  type      = string
  sensitive = true
}
