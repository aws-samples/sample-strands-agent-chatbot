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

variable "orchestration_table_arn" {
  type = string
}

variable "orchestration_table_name" {
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

variable "runtime_request_timeout_seconds" {
  description = "Maximum time to wait for one synchronous mailbox drain"
  type        = number
  default     = 540

  validation {
    condition = (
      var.runtime_request_timeout_seconds >= 60 &&
      var.runtime_request_timeout_seconds <= 870
    )
    error_message = "runtime_request_timeout_seconds must be between 60 and 870."
  }
}
