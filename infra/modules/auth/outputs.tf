output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "user_pool_arn" {
  value = aws_cognito_user_pool.main.arn
}

output "domain" {
  value = aws_cognito_user_pool_domain.main.domain
}

output "domain_url" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "issuer_url" {
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

output "discovery_url" {
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/openid-configuration"
}

output "app_client_id" {
  value = aws_cognito_user_pool_client.app.id
}

output "app_client_secret" {
  value     = aws_cognito_user_pool_client.app.client_secret
  sensitive = true
}

output "web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "m2m_client_id" {
  value = aws_cognito_user_pool_client.m2m.id
}

output "m2m_client_secret" {
  value     = aws_cognito_user_pool_client.m2m.client_secret
  sensitive = true
}
