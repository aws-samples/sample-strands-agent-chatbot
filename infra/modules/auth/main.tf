resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-${var.environment}"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  tags = {
    Component = "auth"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.project_name}-${var.environment}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

# Single resource server. Audience boundary is enforced via allowed_clients
# on each Runtime/Gateway authorizer, not via per-resource scopes.
# Phase 2 may split this (e.g., runtime/invoke vs gateway/invoke).
resource "aws_cognito_resource_server" "agentcore" {
  identifier   = "agentcore"
  name         = "AgentCore API"
  user_pool_id = aws_cognito_user_pool.main.id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke AgentCore Runtime and Gateway"
  }
}

# User-facing client (authorization_code + PKCE via Hosted UI).
resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.project_name}-app-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = true

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile", "agentcore/invoke"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  id_token_validity      = 24
  access_token_validity  = 24
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }

  depends_on = [aws_cognito_resource_server.agentcore]
}

# Browser SPA client (no secret).
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project_name}-web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  supported_identity_providers = ["COGNITO"]

  id_token_validity      = 24
  access_token_validity  = 24
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }
}

# Service principal for background/batch callers (no user context).
resource "aws_cognito_user_pool_client" "m2m" {
  name         = "${var.project_name}-m2m-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = true

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["agentcore/invoke"]
  supported_identity_providers         = ["COGNITO"]

  depends_on = [aws_cognito_resource_server.agentcore]
}

resource "aws_ssm_parameter" "user_pool_id" {
  name  = "/${var.project_name}/${var.environment}/auth/user-pool-id"
  type  = "String"
  value = aws_cognito_user_pool.main.id
}

resource "aws_ssm_parameter" "app_client_id" {
  name  = "/${var.project_name}/${var.environment}/auth/app-client-id"
  type  = "String"
  value = aws_cognito_user_pool_client.app.id
}

resource "aws_ssm_parameter" "web_client_id" {
  name  = "/${var.project_name}/${var.environment}/auth/web-client-id"
  type  = "String"
  value = aws_cognito_user_pool_client.web.id
}

resource "aws_ssm_parameter" "m2m_client_id" {
  name  = "/${var.project_name}/${var.environment}/auth/m2m-client-id"
  type  = "String"
  value = aws_cognito_user_pool_client.m2m.id
}


resource "aws_ssm_parameter" "issuer_url" {
  name  = "/${var.project_name}/${var.environment}/auth/issuer-url"
  type  = "String"
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}
