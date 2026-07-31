locals {
  prefix        = "${var.project_name}-${var.environment}-chat"
  ecr_repo_name = "chatbot-frontend"
  frontend_dir  = "${var.repo_root}/${var.frontend_rel_path}"
  s3_source_key = "frontend-source.zip"

  # Hash only source files that affect the build — avoids fileset scanning
  # node_modules/.next which can trigger "inconsistent result" errors.
  frontend_files = concat(
    [for f in fileset(local.frontend_dir, "src/**") : f],
    [for f in fileset(local.frontend_dir, "public/**") : f],
    [for f in fileset(local.frontend_dir, "*.{json,js,ts,mjs,cjs}") : f],
    [for f in fileset(local.frontend_dir, "Dockerfile") : f],
  )

  source_hash = sha1(join("", [
    for f in local.frontend_files : try(filesha1("${local.frontend_dir}/${f}"), "")
  ]))

  build_arg_extra = join(" ", [
    for k, v in var.frontend_build_args : "--build-arg ${k}=${v}"
  ])
}

locals {
  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids
}

# ============================================================
# ECR + source S3 + CodeBuild
# ============================================================

resource "aws_ecr_repository" "frontend" {
  name                 = local.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "frontend" {
  repository = aws_ecr_repository.frontend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "source" {
  bucket        = "${local.prefix}-src-${var.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_lifecycle_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration { days = 7 }
  }
}

resource "null_resource" "upload_source" {
  triggers = { source_hash = local.source_hash }

  provisioner "local-exec" {
    working_dir = local.frontend_dir
    command     = <<-EOT
      set -e
      rm -f /tmp/${local.prefix}-src.zip
      zip -rq /tmp/${local.prefix}-src.zip . \
        -x 'node_modules/*' '.next/*' '.git/*' '__tests__/*' '*.log' '.DS_Store'
      aws s3 cp /tmp/${local.prefix}-src.zip \
        s3://${aws_s3_bucket.source.bucket}/${local.s3_source_key} \
        --region ${var.aws_region}
    EOT
  }
}

resource "aws_iam_role" "codebuild" {
  name = "${local.prefix}-cb"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "codebuild" {
  name = "cb-policy"
  role = aws_iam_role.codebuild.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
        ]
        Resource = aws_ecr_repository.frontend.arn
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/codebuild/*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/codebuild/*:log-stream:*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.source.arn, "${aws_s3_bucket.source.arn}/*"]
      },
    ]
  })
}

resource "aws_codebuild_project" "frontend" {
  name          = "${local.prefix}-build"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 30

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_MEDIUM"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPO_URI"
      value = aws_ecr_repository.frontend.repository_url
    }
    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = var.account_id
    }
    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }
    environment_variable {
      name  = "COGNITO_USER_POOL_ID"
      value = var.cognito_user_pool_id
    }
    environment_variable {
      name  = "COGNITO_CLIENT_ID"
      value = var.cognito_user_pool_client_id
    }
    environment_variable {
      name  = "BUILD_ARG_EXTRA"
      value = local.build_arg_extra
    }
    environment_variable {
      name  = "SOURCE_HASH"
      value = local.source_hash
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.source.bucket}/${local.s3_source_key}"
    buildspec = <<-BUILDSPEC
      version: 0.2
      phases:
        pre_build:
          commands:
            - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
        build:
          commands:
            - |
              docker build \
                --build-arg NEXT_PUBLIC_AWS_REGION=$AWS_DEFAULT_REGION \
                --build-arg NEXT_PUBLIC_COGNITO_USER_POOL_ID=$COGNITO_USER_POOL_ID \
                --build-arg NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID=$COGNITO_CLIENT_ID \
                $BUILD_ARG_EXTRA \
                -t $ECR_REPO_URI:latest -t $ECR_REPO_URI:$SOURCE_HASH .
        post_build:
          commands:
            - docker push $ECR_REPO_URI:latest
            - docker push $ECR_REPO_URI:$SOURCE_HASH
    BUILDSPEC
  }
}

resource "null_resource" "codebuild_trigger" {
  triggers = { source_hash = local.source_hash }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      if aws ecr describe-images --repository-name "${local.ecr_repo_name}" \
        --image-ids imageTag="${local.source_hash}" --region ${var.aws_region} >/dev/null 2>&1; then
        echo "Frontend image ${local.source_hash} already built, skipping."
        exit 0
      fi
      BUILD_ID=$(aws codebuild start-build --project-name "${aws_codebuild_project.frontend.name}" \
        --region ${var.aws_region} --query 'build.id' --output text)
      for i in $(seq 1 180); do
        STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region ${var.aws_region} \
          --query 'builds[0].buildStatus' --output text)
        echo "  build status: $STATUS"
        case "$STATUS" in
          SUCCEEDED) exit 0 ;;
          FAILED|FAULT|STOPPED|TIMED_OUT) exit 1 ;;
        esac
        sleep 10
      done
      exit 1
    EOT
  }

  depends_on = [aws_codebuild_project.frontend, null_resource.upload_source, aws_iam_role_policy.codebuild]
}

# ============================================================
# ECS Cluster + Task + Service
# ============================================================

resource "aws_ecs_cluster" "this" {
  name = "chatbot-cluster"
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/chatbot-frontend"
  retention_in_days = 7
}

resource "aws_iam_role" "ecs_execution" {
  name = "${local.prefix}-ecs-exec"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  count = var.bedrock_api_key_secret_arn != "" ? 1 : 0

  name = "${local.prefix}-ecs-exec-secrets"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.bedrock_api_key_secret_arn
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.prefix}-ecs-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "chat-task-policy"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntimeForUser",
          "bedrock-agentcore:*",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:runtime/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:memory/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:gateway/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:browser/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:browser-custom/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:code-interpreter/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:code-interpreter-custom/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:workload-identity-directory/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:token-vault/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:registry/*",
        ]
      },
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:${var.aws_region}:${var.account_id}:*",
        ]
      },
      {
        Effect = "Allow"
        Action = ["bedrock-agentcore:CompleteResourceTokenAuth"]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:token-vault/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:workload-identity-directory/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:bedrock-agentcore-*"
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/${var.project_name}/${var.environment}/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
          "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
        ]
        Resource = [var.users_table_arn, var.sessions_table_arn, "${var.sessions_table_arn}/index/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeGateway", "bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGateways"]
        Resource = "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:gateway/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [var.artifact_bucket_arn, "${var.artifact_bucket_arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "cloudwatch:PutMetricData"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "chatbot-frontend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name         = "chatbot-frontend"
    image        = "${aws_ecr_repository.frontend.repository_url}:${local.source_hash}"
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment = [
      { name = "NODE_ENV", value = "production" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "NEXT_PUBLIC_AWS_REGION", value = var.aws_region },
      { name = "PROJECT_NAME", value = var.project_name },
      { name = "ENVIRONMENT", value = var.environment },
      { name = "NEXT_PUBLIC_COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
      { name = "NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID", value = var.cognito_user_pool_client_id },
      { name = "DYNAMODB_USERS_TABLE", value = var.users_table_name },
      { name = "DYNAMODB_SESSIONS_TABLE", value = var.sessions_table_name },
      { name = "ARTIFACT_BUCKET", value = var.artifact_bucket_name },
      { name = "MEMORY_ID", value = var.memory_id },
      { name = "MCP_GATEWAY_URL", value = var.gateway_url },
      { name = "ORCHESTRATOR_RUNTIME_ARN", value = var.orchestrator_runtime_arn },
      { name = "AGENTCORE_RUNTIME_ARN", value = var.orchestrator_runtime_arn },
      { name = "AGENTCORE_RUNTIME_URL", value = var.orchestrator_runtime_url },
      { name = "SOURCE_HASH", value = local.source_hash },
    ]
    secrets = var.bedrock_api_key_secret_arn != "" ? [{
      name      = "AWS_BEARER_TOKEN_BEDROCK"
      valueFrom = var.bedrock_api_key_secret_arn
    }] : []
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.frontend.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "chatbot-frontend"
      }
    }
  }])

  depends_on = [
    null_resource.codebuild_trigger,
    aws_iam_role_policy_attachment.ecs_execution,
    aws_iam_role_policy.ecs_execution_secrets,
  ]
}

# ALB ingress uses CloudFront prefix list. Keep ingress inline to avoid the
# inline/standalone mix that races during destroy.
resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "Chatbot ALB (HTTP from CloudFront prefix list)"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "ecs" {
  name        = "${local.prefix}-ecs"
  description = "Chatbot ECS tasks"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "this" {
  name               = "chatbot-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.subnet_ids
  idle_timeout       = 3600
}

resource "aws_lb_target_group" "frontend" {
  name        = "chatbot-frontend-tg"
  port        = 3000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = 30
  health_check {
    path                = "/api/health"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 10
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_ecs_service" "frontend" {
  name                               = "chatbot-frontend"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.frontend.arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "chatbot-frontend"
    container_port   = 3000
  }

  depends_on = [aws_lb_listener.http]
}

# ============================================================
# CloudFront
# ============================================================

resource "aws_cloudfront_origin_request_policy" "this" {
  name    = "${local.prefix}-origin"
  comment = "Forward all headers/cookies/query for session management"

  cookies_config {
    cookie_behavior = "all"
  }
  headers_config {
    header_behavior = "allViewer"
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Chatbot ${var.environment} (HTTPS)"
  price_class     = "PriceClass_100"

  origin {
    domain_name = aws_lb.this.dns_name
    origin_id   = "chatbot-alb"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id         = "chatbot-alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    compress                 = true
    origin_request_policy_id = aws_cloudfront_origin_request_policy.this.id
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Register CloudFront URL as OAuth callback + update Cognito client
resource "aws_ssm_parameter" "oauth_callback_url" {
  name      = "/${var.project_name}/${var.environment}/mcp/oauth2-callback-url"
  type      = "String"
  value     = "https://${aws_cloudfront_distribution.this.domain_name}/oauth-complete"
  overwrite = true
}
