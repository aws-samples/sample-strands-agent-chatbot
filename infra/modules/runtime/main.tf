locals {
  resource_prefix   = "${var.project_name}-${var.environment}-${var.component_name}"
  runtime_name_safe = replace("${var.project_name}_${var.environment}_${var.component_name}", "-", "_")
  ecr_repo_name     = "${var.project_name}/${var.component_name}"
  s3_key            = "codebuild/${var.component_name}/source.zip"

  # Exclude build artifacts / caches / vendored deps (including symlinks under node_modules).
  source_files = [
    for f in fileset("${var.repo_root}/${var.build_context}", "**") : f
    if !can(regex("(^|/)(cdk\\.out|node_modules|\\.venv|venv|__pycache__|\\.ruff_cache|\\.pytest_cache|\\.git|\\.next|\\.terraform)(/|$)", f))
    && !can(regex("^sessions(/|$)", f))
    && !can(regex("^tests(/|$)", f))
    && f != "requirements-dev.txt"
    && f != "pytest.ini"
    && !can(regex("(^|/)\\.DS_Store$", f))
    && !can(regex("\\.pyc$", f))
    && !can(regex("\\.log$", f))
  ]

  source_hash = sha1(join("", [
    for f in local.source_files :
    filesha1("${var.repo_root}/${var.build_context}/${f}")
  ]))

  runtime_protocol = (
    var.runtime_type == "component" || var.runtime_type == "mcp_3lo" ? "MCP" :
    var.runtime_type == "a2a_agent" ? "A2A" : "HTTP"
  )
}

# ============================================================
# ECR
# ============================================================

resource "aws_ecr_repository" "this" {
  name                 = local.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Component = var.component_name
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
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

# ============================================================
# S3 source bucket + hash-gated upload
# ============================================================

resource "aws_s3_bucket" "source" {
  bucket        = "${local.resource_prefix}-src-${var.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_lifecycle_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    id     = "expire-old-sources"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
  }
}

resource "null_resource" "upload_source" {
  triggers = {
    source_hash = local.source_hash
  }

  provisioner "local-exec" {
    working_dir = var.repo_root
    command     = <<-EOT
      set -e
      cd "${var.build_context}"
      rm -f /tmp/${var.component_name}-source.zip
      zip -r /tmp/${var.component_name}-source.zip . \
        -x 'venv/*' '.venv/*' '__pycache__/*' '*/__pycache__/*' \
           '.ruff_cache/*' '*/.ruff_cache/*' '.pytest_cache/*' '*/.pytest_cache/*' \
           'sessions/*' 'tests/*' 'requirements-dev.txt' 'pytest.ini' \
           '*.pyc' '.git/*' 'node_modules/*' '.DS_Store' '*.log' 'cdk/*' 'cdk.out/*'
      aws s3 cp /tmp/${var.component_name}-source.zip \
        s3://${aws_s3_bucket.source.bucket}/${local.s3_key} \
        --region ${var.aws_region}
    EOT
  }

  depends_on = [aws_s3_bucket.source]
}

# ============================================================
# CodeBuild (Docker build & push, ARM64)
# ============================================================

resource "aws_iam_role" "codebuild" {
  name = "${local.resource_prefix}-codebuild"

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
  name = "codebuild-policy"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ]
        Resource = aws_ecr_repository.this.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetBucketAcl", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.source.arn, "${aws_s3_bucket.source.arn}/*"]
      },
    ]
  })
}

resource "aws_codebuild_project" "this" {
  name          = "${local.resource_prefix}-build"
  description   = "Docker build for ${var.component_name}"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 15

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/amazonlinux2-aarch64-standard:3.0"
    type                        = "ARM_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "ECR_REPO_URI"
      value = aws_ecr_repository.this.repository_url
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
      name  = "DOCKERFILE_PATH"
      value = var.dockerfile_path
    }
    environment_variable {
      name  = "SOURCE_HASH"
      value = local.source_hash
    }
  }

  source {
    type     = "S3"
    location = "${aws_s3_bucket.source.bucket}/${local.s3_key}"

    buildspec = <<-BUILDSPEC
      version: 0.2
      phases:
        pre_build:
          commands:
            - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
        build:
          commands:
            - docker build -t $ECR_REPO_URI:latest -t $ECR_REPO_URI:$SOURCE_HASH -f $DOCKERFILE_PATH .
        post_build:
          commands:
            - docker push $ECR_REPO_URI:latest
            - docker push $ECR_REPO_URI:$SOURCE_HASH
    BUILDSPEC
  }

  tags = {
    Component = var.component_name
  }
}

# Skip CodeBuild if the hash-tagged image already exists in ECR.
# This is the key change-tracking mechanism replacing CDK's Date.now() hack.
resource "null_resource" "codebuild_trigger" {
  triggers = {
    source_hash = local.source_hash
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      if aws ecr describe-images \
        --repository-name "${local.ecr_repo_name}" \
        --image-ids imageTag="${local.source_hash}" \
        --region ${var.aws_region} >/dev/null 2>&1; then
        echo "Image ${local.ecr_repo_name}:${local.source_hash} exists, skipping build."
        exit 0
      fi

      echo "Starting CodeBuild for ${var.component_name}..."
      BUILD_ID=$(aws codebuild start-build \
        --project-name "${aws_codebuild_project.this.name}" \
        --region ${var.aws_region} \
        --query 'build.id' --output text)

      for i in $(seq 1 90); do
        STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region ${var.aws_region} \
          --query 'builds[0].buildStatus' --output text)
        echo "  build status: $STATUS"
        case "$STATUS" in
          SUCCEEDED) exit 0 ;;
          FAILED|FAULT|STOPPED|TIMED_OUT) echo "build failed: $STATUS"; exit 1 ;;
        esac
        sleep 10
      done
      echo "build timeout"
      exit 1
    EOT
  }

  depends_on = [aws_codebuild_project.this, null_resource.upload_source, aws_iam_role_policy.codebuild]
}

# ============================================================
# Execution role
# ============================================================

resource "aws_iam_role" "execution" {
  name = "${local.resource_prefix}-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "execution_base" {
  name = "base-policy"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${var.account_id}:inference-profile/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/${var.project_name}/*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "execution_ddb" {
  count = var.enable_ddb_policy ? 1 : 0
  name  = "ddb-policy"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      var.global_data_table_arn != "" ? [{
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [var.global_data_table_arn]
      }] : [],
      var.user_data_table_arn != "" ? [{
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
          "dynamodb:Query", "dynamodb:Scan",
        ]
        Resource = [var.user_data_table_arn]
      }] : [],
      var.orchestration_table_arn != "" ? [{
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:TransactWriteItems",
          "dynamodb:ConditionCheckItem",
          "dynamodb:Query",
        ]
        Resource = [var.orchestration_table_arn]
      }] : [],
    )
  })
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  name  = "secrets-policy"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

# Orchestrator: invoke Gateway/Registry/Memory + other Runtimes + CodeInterpreter + Browser.
# Note: InvokeGateway kept here for AWS resource permissions, but inbound auth to
# Gateway is JWT — this IAM allows the AWS API action, JWT decides if the caller passes.
resource "aws_iam_role_policy" "orchestrator_extra" {
  count = var.runtime_type == "orchestrator" ? 1 : 0
  name  = "orchestrator-extra"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:SearchRegistryRecords",
          "bedrock-agentcore:ListRegistryRecords",
          "bedrock-agentcore:GetRegistryRecord",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:CreateMemoryRecord",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListSessions",
          "bedrock-agentcore:CreateSession",
          "bedrock-agentcore:GetSession",
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntimeForUser",
          "bedrock-agentcore:GetAgentCard",
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore:GetAgentRuntimeEndpoint",
          "bedrock-agentcore:CompleteResourceTokenAuth",
          "secretsmanager:GetSecretValue",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateCodeInterpreter",
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:DeleteCodeInterpreter",
          "bedrock-agentcore:ListCodeInterpreters",
          "bedrock-agentcore:GetCodeInterpreter",
          "bedrock-agentcore:GetCodeInterpreterSession",
          "bedrock-agentcore:ListCodeInterpreterSessions",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:*:aws:code-interpreter/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:code-interpreter/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:code-interpreter-custom/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateBrowser",
          "bedrock-agentcore:StartBrowserSession",
          "bedrock-agentcore:GetBrowserSession",
          "bedrock-agentcore:UpdateBrowserSession",
          "bedrock-agentcore:UpdateBrowserStream",
          "bedrock-agentcore:StopBrowserSession",
          "bedrock-agentcore:DeleteBrowser",
          "bedrock-agentcore:ListBrowsers",
          "bedrock-agentcore:GetBrowser",
          "bedrock-agentcore:ListBrowserSessions",
          "bedrock-agentcore:ConnectBrowserAutomationStream",
          "bedrock-agentcore:ConnectBrowserLiveViewStream",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:browser/*",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${var.account_id}:browser-custom/*",
        ]
      },
      {
        Sid    = "NovaActWorkflowAccess"
        Effect = "Allow"
        Action = ["nova-act:*"]
        Resource = [
          "arn:aws:nova-act:us-east-1:${var.account_id}:workflow-definition/*",
        ]
      },
    ]
  })
}

# Orchestrator: artifact bucket write (user uploads, generated docs).
resource "aws_iam_role_policy" "orchestrator_artifacts" {
  count = var.runtime_type == "orchestrator" ? 1 : 0
  name  = "orchestrator-artifacts"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"]
      Resource = [var.artifact_bucket_arn, "${var.artifact_bucket_arn}/*"]
    }]
  })
}

resource "aws_iam_role_policy" "orchestrator_workspace_access_points" {
  count = var.enable_workspace_access_points ? 1 : 0
  name  = "workspace-access-points"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3files:CreateAccessPoint",
          "s3files:ListAccessPoints",
        ]
        Resource = var.workspace_file_system_arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3files:DeleteAccessPoint",
          "s3files:GetAccessPoint",
          "s3files:ListTagsForResource",
        ]
        Resource = "${var.workspace_file_system_arn}/access-point/*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "a2a_agent_extra" {
  count = contains(["a2a_agent", "http_agent"], var.runtime_type) ? 1 : 0
  name  = "processing-agent-extra"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateCodeInterpreter",
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:DeleteCodeInterpreter",
          "bedrock-agentcore:ListCodeInterpreters",
          "bedrock-agentcore:GetCodeInterpreter",
          "bedrock-agentcore:GetCodeInterpreterSession",
          "bedrock-agentcore:ListCodeInterpreterSessions",
          "bedrock-agentcore:CreateBrowser",
          "bedrock-agentcore:StartBrowserSession",
          "bedrock-agentcore:ConnectBrowserAutomationStream",
          "bedrock-agentcore:StopBrowserSession",
          "bedrock-agentcore:DeleteBrowser",
        ]
        Resource = "*"
      }],
      var.artifact_bucket_arn != "" ? [{
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [var.artifact_bucket_arn, "${var.artifact_bucket_arn}/*"]
      }] : [],
    )
  })
}

# MCP 3LO: outbound OAuth token vending (Gmail/Google/Notion/GitHub).
resource "aws_iam_role_policy" "mcp_3lo_extra" {
  count = var.runtime_type == "mcp_3lo" ? 1 : 0
  name  = "mcp-3lo-extra"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetResourceOauth2Token",
          "bedrock-agentcore:CreateWorkloadIdentity",
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "read_only_s3" {
  count = length(var.read_only_bucket_arns) > 0 ? 1 : 0
  name  = "read-only-s3"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = flatten([
        for arn in var.read_only_bucket_arns : [arn, "${arn}/*"]
      ])
    }]
  })
}

# ============================================================
# AgentCore Runtime
# ============================================================

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = local.runtime_name_safe
  description        = "Runtime for ${var.component_name}"
  role_arn           = aws_iam_role.execution.arn

  network_configuration {
    network_mode = var.network_mode

    dynamic "network_mode_config" {
      for_each = var.network_mode == "VPC" ? [1] : []
      content {
        subnets         = var.subnet_ids
        security_groups = var.security_group_ids
      }
    }
  }

  protocol_configuration {
    server_protocol = local.runtime_protocol
  }

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.this.repository_url}:${local.source_hash}"
    }
  }

  dynamic "authorizer_configuration" {
    for_each = var.cognito_issuer_url != "" ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url   = "${var.cognito_issuer_url}/.well-known/openid-configuration"
        allowed_clients = var.cognito_allowed_clients
      }
    }
  }

  # Allow the agent to forward the caller's JWT to downstream services (Gateway).
  # This is how user identity propagates end-to-end under the JWT-only auth model.
  dynamic "request_header_configuration" {
    for_each = var.cognito_issuer_url != "" ? [1] : []
    content {
      request_header_allowlist = ["Authorization"]
    }
  }

  environment_variables = merge(
    {
      AWS_REGION   = var.aws_region
      PROJECT_NAME = var.project_name
      ENVIRONMENT  = var.environment
    },
    var.user_data_table_name != "" ? { USER_DATA_TABLE = var.user_data_table_name } : {},
    var.global_data_table_name != "" ? { GLOBAL_DATA_TABLE = var.global_data_table_name } : {},
    var.orchestration_table_name != "" ? {
      SESSION_ORCHESTRATION_TABLE = var.orchestration_table_name
    } : {},
    var.runtime_type == "orchestrator" ? {
      GATEWAY_URL = var.gateway_url
      REGISTRY_ID = var.registry_id
      MEMORY_ID   = var.memory_id
    } : {},
    contains(["a2a_agent", "http_agent"], var.runtime_type) && var.artifact_bucket_name != "" ? {
      ARTIFACT_BUCKET = var.artifact_bucket_name
    } : {},
    var.extra_env_vars,
    {
      # Forces Runtime to re-pull when source changes.
      SOURCE_HASH = local.source_hash
    },
  )

  tags = {
    Component = var.component_name
  }

  depends_on = [null_resource.codebuild_trigger, aws_iam_role.execution]
}

resource "aws_ssm_parameter" "runtime_arn" {
  name  = "/${var.project_name}/${var.environment}/runtimes/${var.component_name}/arn"
  type  = "String"
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

resource "aws_ssm_parameter" "runtime_id" {
  name  = "/${var.project_name}/${var.environment}/runtimes/${var.component_name}/id"
  type  = "String"
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}
