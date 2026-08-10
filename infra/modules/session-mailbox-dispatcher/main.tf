locals {
  function_name = "${var.project_name}-${var.environment}-session-mailbox-dispatcher"
  worker_name   = "${local.function_name}-worker"
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
  name = "stream-ingress"
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
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeStream",
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:ListStreams",
        ]
        Resource = var.orchestration_stream_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.wake.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = "${var.orchestration_table_arn}/index/DelegationWorkIndex"
      },
    ]
  })
}

resource "aws_iam_role" "worker" {
  name = "${local.worker_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "worker" {
  name = "queue-worker"
  role = aws_iam_role.worker.id
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
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource = aws_sqs_queue.wake.arn
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
        ]
        Resource = var.orchestration_table_arn
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${local.worker_name}"
  retention_in_days = 14
}

resource "aws_sqs_queue" "wake_dead_letter" {
  name                      = "${local.function_name}-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "wake" {
  name                       = "${local.function_name}.fifo"
  fifo_queue                 = true
  message_retention_seconds  = 345600
  visibility_timeout_seconds = (var.runtime_request_timeout_seconds + 30) * 6
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.wake_dead_letter.arn
    maxReceiveCount     = 8
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "wake_dead_letter" {
  queue_url = aws_sqs_queue.wake_dead_letter.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.wake.arn]
  })
}

resource "aws_lambda_function" "this" {
  function_name = local.function_name
  role          = aws_iam_role.this.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  environment {
    variables = {
      ORCHESTRATION_TABLE_NAME = var.orchestration_table_name
      WAKE_QUEUE_URL           = aws_sqs_queue.wake.url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy.this,
  ]
}

resource "aws_lambda_function" "worker" {
  function_name = local.worker_name
  role          = aws_iam_role.worker.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["arm64"]
  timeout       = var.runtime_request_timeout_seconds + 30
  memory_size   = 256

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  environment {
    variables = {
      AGENTCORE_RUNTIME_URL    = var.agentcore_runtime_url
      COGNITO_TOKEN_URL        = "${var.cognito_domain_url}/oauth2/token"
      M2M_SECRET_ARN           = aws_secretsmanager_secret.m2m.arn
      ORCHESTRATION_TABLE_NAME = var.orchestration_table_name
      WAKE_QUEUE_URL           = aws_sqs_queue.wake.url
      RUNTIME_REQUEST_TIMEOUT_SECONDS = tostring(
        var.runtime_request_timeout_seconds
      )
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.worker,
    aws_iam_role_policy.worker,
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

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn        = aws_sqs_queue.wake.arn
  function_name           = aws_lambda_function.worker.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}

resource "aws_cloudwatch_event_rule" "delegation_reconcile" {
  name                = "${var.project_name}-${var.environment}-delegation-reconcile"
  schedule_expression = "rate(2 minutes)"
}

resource "aws_cloudwatch_event_target" "delegation_reconcile" {
  rule      = aws_cloudwatch_event_rule.delegation_reconcile.name
  target_id = "delegation-reconcile"
  arn       = aws_lambda_function.this.arn
}

resource "aws_lambda_permission" "delegation_reconcile" {
  statement_id  = "AllowDelegationReconcile"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.delegation_reconcile.arn
}
