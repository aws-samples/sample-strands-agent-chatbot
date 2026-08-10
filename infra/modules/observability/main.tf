# AgentCore Observability — CloudWatch Vended Logs + X-Ray Traces

locals {
  prefix        = "${var.project_name}-${var.environment}-${var.resource_name}"
  source_suffix = var.source_name_suffix != "" ? "-${var.source_name_suffix}" : ""
}

# ============================================================
# CloudWatch Log Group (destination for APPLICATION_LOGS)
# ============================================================

resource "aws_cloudwatch_log_group" "logs" {
  name              = "/aws/vendedlogs/bedrock-agentcore/${var.resource_name}/${var.project_name}-${var.environment}"
  retention_in_days = var.log_retention_days
}

# ============================================================
# Delivery Sources (what to collect)
# ============================================================

resource "aws_cloudwatch_log_delivery_source" "logs" {
  name         = "${local.prefix}-logs${local.source_suffix}"
  log_type     = "APPLICATION_LOGS"
  resource_arn = var.resource_arn

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_delivery_source" "traces" {
  name         = "${local.prefix}-traces${local.source_suffix}"
  log_type     = "TRACES"
  resource_arn = var.resource_arn

  lifecycle {
    create_before_destroy = true
  }
}

# ============================================================
# Delivery Destinations (where to send)
# ============================================================

resource "aws_cloudwatch_log_delivery_destination" "logs" {
  name = "${local.prefix}-logs-dest"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.logs.arn
  }
}

resource "aws_cloudwatch_log_delivery_destination" "traces" {
  name                      = "${local.prefix}-traces-dest"
  delivery_destination_type = "XRAY"
}

# ============================================================
# Deliveries (connect source -> destination)
# ============================================================

resource "aws_cloudwatch_log_delivery" "logs" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.logs.arn
}

resource "aws_cloudwatch_log_delivery" "traces" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.traces.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.traces.arn
}
