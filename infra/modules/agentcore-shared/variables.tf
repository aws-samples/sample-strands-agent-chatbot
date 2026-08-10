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

variable "code_interpreter_execution_role_arn" {
  type    = string
  default = ""
}

variable "code_interpreter_subnet_ids" {
  type    = list(string)
  default = []
}

variable "code_interpreter_security_group_ids" {
  type    = list(string)
  default = []
}

variable "nova_act_workflow_name" {
  description = "Nova Act Workflow Definition Name. Create via: aws nova-act create-workflow-definition --name <name>."
  type        = string
  default     = ""
}
