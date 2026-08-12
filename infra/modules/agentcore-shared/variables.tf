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
  type = string

  validation {
    condition     = var.code_interpreter_execution_role_arn != ""
    error_message = "code_interpreter_execution_role_arn is required."
  }
}

variable "code_interpreter_subnet_ids" {
  type = list(string)

  validation {
    condition     = length(var.code_interpreter_subnet_ids) > 0
    error_message = "At least one Code Interpreter subnet is required."
  }
}

variable "code_interpreter_security_group_ids" {
  type = list(string)

  validation {
    condition     = length(var.code_interpreter_security_group_ids) > 0
    error_message = "At least one Code Interpreter security group is required."
  }
}

variable "nova_act_workflow_name" {
  description = "Nova Act Workflow Definition Name. Create via: aws nova-act create-workflow-definition --name <name>."
  type        = string
  default     = ""
}
