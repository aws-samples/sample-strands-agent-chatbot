# AgentCore OAuth2 Credential Providers for MCP 3LO Runtime.
#
# Each provider is optional — enabled only when both client_id and client_secret
# are supplied. Callback URL is not exposed as a Terraform attribute, so we fetch
# it post-create via AWS CLI and write it to SSM. The MCP 3LO runtime reads
# this parameter when starting a user-federation flow.
#
# IMPORTANT: Once created, providers must NOT be destroyed — each has a unique
# callback UUID registered in the external OAuth app (Google Console, GitHub, etc).
# Recreating the provider changes the UUID, breaking OAuth flows until the user
# re-registers the new URL. prevent_destroy guards against accidental deletion.

locals {
  google_enabled = var.google_client_id != "" && var.google_client_secret != ""
  github_enabled = var.github_client_id != "" && var.github_client_secret != ""
  notion_enabled = var.notion_client_id != "" && var.notion_client_secret != ""
}

resource "aws_bedrockagentcore_oauth2_credential_provider" "google" {
  count = local.google_enabled ? 1 : 0

  name                       = "google-oauth-provider"
  credential_provider_vendor = "GoogleOauth2"

  oauth2_provider_config {
    google_oauth2_provider_config {
      client_id     = var.google_client_id
      client_secret = var.google_client_secret
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_bedrockagentcore_oauth2_credential_provider" "github" {
  count = local.github_enabled ? 1 : 0

  name = "github-oauth-provider"

  # Built-in vendor, not CustomOauth2: GitHub publishes no discovery document,
  # so a custom provider can't describe the authorize request and Identity
  # rejects it with 400 "Invalid request".
  credential_provider_vendor = "GithubOauth2"

  oauth2_provider_config {
    github_oauth2_provider_config {
      client_id     = var.github_client_id
      client_secret = var.github_client_secret
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_bedrockagentcore_oauth2_credential_provider" "notion" {
  count = local.notion_enabled ? 1 : 0

  name                       = "notion-oauth-provider"
  credential_provider_vendor = "CustomOauth2"

  oauth2_provider_config {
    custom_oauth2_provider_config {
      client_id     = var.notion_client_id
      client_secret = var.notion_client_secret

      oauth_discovery {
        authorization_server_metadata {
          issuer                 = "https://api.notion.com"
          authorization_endpoint = "https://api.notion.com/v1/oauth/authorize"
          token_endpoint         = "https://api.notion.com/v1/oauth/token"
        }
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# External OAuth apps must register the provider callback URL returned by
# AgentCore (https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<id>).
# The CloudFront /oauth-complete URL stored in SSM is a separate return URL:
# AgentCore redirects the user's browser there after provider authorization.
