locals {
  function_name = "${var.project_name}-${var.environment}-session-mailbox-dispatcher"
  zip_path      = "${path.module}/.build/session-mailbox-dispatcher.zip"
}

data "archive_file" "this" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = local.zip_path
  excludes    = ["test_*.py", "__pycache__/*"]
}

resource "aws_secretsmanager_secret" "m2m" {
  name                    = "${var.project_name}/${var.environment}/session-mailbox-dispatcher/m2m"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "m2m" {
  secret_id = aws_secretsmanager_secret.m2m.id
  secret_string = jsonencode({
    clientId     = var.m2m_client_id
    clientSecret = var.m2m_client_secret
  })
}

resource "aws_iam_role" "this" {
  name = "${local.function_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "this" {
  name = "dispatcher"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.m2m.arn
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeStream",
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:ListStreams",
        ]
        Resource = var.orchestration_stream_arn
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "this" {
  function_name = local.function_name
  role          = aws_iam_role.this.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["arm64"]
  timeout       = 45
  memory_size   = 256

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  environment {
    variables = {
      AGENTCORE_RUNTIME_URL = var.agentcore_runtime_url
      COGNITO_TOKEN_URL     = "${var.cognito_domain_url}/oauth2/token"
      M2M_SECRET_ARN        = aws_secretsmanager_secret.m2m.arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy.this,
    aws_secretsmanager_secret_version.m2m,
  ]
}

resource "aws_lambda_event_source_mapping" "this" {
  event_source_arn                   = var.orchestration_stream_arn
  function_name                      = aws_lambda_function.this.arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  maximum_record_age_in_seconds      = -1
  maximum_retry_attempts             = -1
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]
}
