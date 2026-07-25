variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "callback_urls" {
  type    = list(string)
  default = ["http://localhost:3000/api/auth/callback"]
}

variable "logout_urls" {
  type    = list(string)
  default = ["http://localhost:3000"]
}
