resource "aws_ecs_cluster" "telegram" {
  name = "${local.prefix}-cluster"
}

resource "aws_cloudwatch_log_group" "telegram" {
  name              = "/ecs/telegram-adapter"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "telegram" {
  family                   = "${local.prefix}-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name         = "telegram-adapter"
    image        = "${aws_ecr_repository.telegram.repository_url}:${local.source_hash}"
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [
      { name = "TELEGRAM_BOT_TOKEN", value = var.telegram_bot_token },
      { name = "RUNTIME_INVOCATION_URL", value = var.runtime_invocation_url },
      { name = "COGNITO_TOKEN_URL", value = "${var.cognito_domain_url}/oauth2/token" },
      { name = "M2M_CLIENT_ID", value = var.m2m_client_id },
      { name = "M2M_CLIENT_SECRET", value = var.m2m_client_secret },
      { name = "DEDUP_TABLE_NAME", value = aws_dynamodb_table.dedup.name },
      { name = "ALLOWED_USER_IDS", value = var.allowed_user_ids },
      { name = "OWNER_USER_ID", value = var.owner_user_id },
      { name = "ARTIFACT_BUCKET", value = var.artifact_bucket_arn != "" ? regex("arn:aws:s3:::(.+)", var.artifact_bucket_arn)[0] : "" },
      { name = "LOG_LEVEL", value = "info" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.telegram.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "telegram"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "wget -qO- http://localhost:8080/health || exit 1"]
      interval    = 30
      timeout     = 5
      startPeriod = 15
      retries     = 3
    }
  }])

  depends_on = [null_resource.codebuild_trigger]
}

resource "aws_ecs_service" "telegram" {
  name                               = "${local.prefix}-service"
  cluster                            = aws_ecs_cluster.telegram.id
  task_definition                    = aws_ecs_task_definition.telegram.arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }
}
