output "aws_region" {
  value = var.aws_region
}

output "cognito_user_pool_id" {
  value = module.auth.user_pool_id
}

output "cognito_issuer_url" {
  value = module.auth.issuer_url
}

output "cognito_app_client_id" {
  value = module.auth.app_client_id
}

output "cognito_web_client_id" {
  value = module.auth.web_client_id
}

output "cognito_m2m_client_id" {
  value = module.auth.m2m_client_id
}

output "mcp_3lo_runtime_arn" {
  value = module.runtime_mcp_3lo.runtime_arn
}

output "mcp_3lo_invocation_url" {
  value = module.runtime_mcp_3lo.runtime_invocation_url
}

output "memory_id" {
  value = module.memory.memory_id
}

output "users_table_name" {
  value = module.data.users_table_name
}

output "sessions_table_name" {
  value = module.data.sessions_table_name
}

output "gateway_id" {
  value = module.gateway.gateway_id
}

output "gateway_url" {
  value = module.gateway.gateway_url
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "code_agent_runtime_arn" {
  value = module.runtime_code_agent.runtime_arn
}

output "research_agent_runtime_arn" {
  value = module.runtime_research_agent.runtime_arn
}

output "orchestrator_runtime_arn" {
  value = module.runtime_orchestrator.runtime_arn
}

output "orchestrator_invocation_url" {
  value = module.runtime_orchestrator.runtime_invocation_url
}

output "chat_cloudfront_url" {
  value = module.chat.cloudfront_url
}

output "chat_alb_dns" {
  value = module.chat.alb_dns
}

output "chat_cognito_login_url" {
  value = module.chat.cognito_login_url
}
