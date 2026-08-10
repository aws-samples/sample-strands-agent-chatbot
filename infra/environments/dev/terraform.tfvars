aws_region                = "us-west-2"
project_name              = "strands-agent-chatbot"
environment               = "dev"
network_mode              = "PUBLIC"
telegram_allowed_user_ids = "8795210678,8680518374"
code_interpreter_supported_az_ids = [
  "usw2-az1",
  "usw2-az2",
  "usw2-az3",
]
code_interpreter_private_subnets = {
  "usw2-az1" = "172.31.64.0/24"
  "usw2-az2" = "172.31.65.0/24"
  "usw2-az3" = "172.31.66.0/24"
}
