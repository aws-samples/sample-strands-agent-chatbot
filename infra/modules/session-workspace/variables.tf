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

variable "artifact_bucket_arn" {
  type = string
}

variable "artifact_bucket_name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "code_interpreter_private_subnets" {
  description = "Availability zone ID to private subnet CIDR mapping for Code Interpreter NAT egress. Empty disables managed NAT egress."
  type        = map(string)
  default     = {}
}
