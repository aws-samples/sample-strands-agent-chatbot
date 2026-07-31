locals {
  gateway_name = "${var.project_name}-${var.environment}-gateway"

  # Tool schemas come from the same YAML set the Registry module consumes.
  # Single source of truth = infra/registry/definitions/mcp/*.yaml.
  _mcp_defs_root = "${path.module}/../../registry/definitions/mcp"
  _mcp_files     = fileset(local._mcp_defs_root, "*.yaml")
  _all_schemas = {
    for f in local._mcp_files :
    trimsuffix(f, ".yaml") => yamldecode(file("${local._mcp_defs_root}/${f}"))
  }

  # Skip schemas for Lambdas that aren't deployed (e.g., Tavily/Google when API key is absent).
  tool_schemas = {
    for k, v in local._all_schemas :
    k => v if contains(keys(var.lambda_tool_arns), k)
  }
}

# ============================================================
# Gateway IAM role (invokes Lambda tools)
# ============================================================

resource "aws_iam_role" "gateway" {
  name = "${local.gateway_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "gateway" {
  name = "gateway-policy"
  role = aws_iam_role.gateway.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      length(var.lambda_tool_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = values(var.lambda_tool_arns)
      }] : [],
      [{
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJwt",
          "bedrock-agentcore:GetResourceOauth2Token",
        ]
        Resource = "*"
      }],
    )
  })
}

# ============================================================
# Gateway (CUSTOM_JWT inbound, MCP protocol)
# ============================================================

resource "aws_bedrockagentcore_gateway" "this" {
  name        = local.gateway_name
  description = "MCP Gateway for ${var.project_name}"
  role_arn    = aws_iam_role.gateway.arn

  authorizer_type = "CUSTOM_JWT"
  protocol_type   = "MCP"

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = "${var.cognito_issuer_url}/.well-known/openid-configuration"
      allowed_clients = var.cognito_allowed_clients
    }
  }

  protocol_configuration {
    mcp {
      instructions       = "MCP Gateway for ${var.project_name} tool integration"
      supported_versions = ["2025-11-25"]

      session_configuration {
        session_timeout_in_seconds = 3600
      }

      streaming_configuration {
        enable_response_streaming = true
      }
    }
  }

  exception_level = "DEBUG"

  tags = {
    Component = "gateway"
  }

  lifecycle {
    ignore_changes = [description]
  }
}

# IAM trust policy propagation delay — Gateway service needs time to
# recognize AssumeRole permission on the role before targets can be created.
resource "time_sleep" "wait_for_iam_propagation" {
  depends_on      = [aws_bedrockagentcore_gateway.this]
  create_duration = "5s"
}

# ============================================================
# Lambda-backed Gateway Targets (one per tool in tool-schemas.json)
# ============================================================

resource "aws_bedrockagentcore_gateway_target" "lambda" {
  for_each = local.tool_schemas

  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  name               = each.key
  description        = each.value.description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = var.lambda_tool_arns[each.key]
        tool_schema {
          dynamic "inline_payload" {
            for_each = each.value.tools
            content {
              name        = inline_payload.value.name
              description = inline_payload.value.description
              input_schema {
                type        = inline_payload.value.input_type
                description = lookup(inline_payload.value, "input_description", null)

                dynamic "property" {
                  for_each = inline_payload.value.properties
                  content {
                    name        = property.value.name
                    type        = property.value.type
                    description = lookup(property.value, "description", null)
                    required    = lookup(property.value, "required", false)
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [time_sleep.wait_for_iam_propagation]
}

# ============================================================
# SSM
# ============================================================

resource "aws_ssm_parameter" "gateway_url" {
  name  = "/${var.project_name}/${var.environment}/mcp/gateway-url"
  type  = "String"
  value = aws_bedrockagentcore_gateway.this.gateway_url
}

resource "aws_ssm_parameter" "gateway_id" {
  name  = "/${var.project_name}/${var.environment}/mcp/gateway-id"
  type  = "String"
  value = aws_bedrockagentcore_gateway.this.gateway_id
}
