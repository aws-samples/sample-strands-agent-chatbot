resource "aws_ecr_repository" "telegram" {
  name                 = local.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "telegram" {
  repository = aws_ecr_repository.telegram.name
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
  bucket        = "${local.prefix}-src-${local.account_id}"
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
    working_dir = local.app_dir
    command     = <<-EOT
      set -e
      rm -f /tmp/${local.prefix}-src.zip
      zip -rq /tmp/${local.prefix}-src.zip . \
        -x 'node_modules/*' 'dist/*' '.git/*' '*.log' '.DS_Store'
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
        Resource = aws_ecr_repository.telegram.arn
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/codebuild/*",
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/codebuild/*:log-stream:*",
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

resource "aws_codebuild_project" "telegram" {
  name          = "${local.prefix}-build"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 15

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPO_URI"
      value = aws_ecr_repository.telegram.repository_url
    }
    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = local.account_id
    }
    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
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
            - docker build -t $ECR_REPO_URI:latest -t $ECR_REPO_URI:$SOURCE_HASH .
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
        echo "Telegram image ${local.source_hash} already built, skipping."
        exit 0
      fi
      BUILD_ID=$(aws codebuild start-build --project-name "${aws_codebuild_project.telegram.name}" \
        --region ${var.aws_region} --query 'build.id' --output text)
      for i in $(seq 1 90); do
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

  depends_on = [aws_codebuild_project.telegram, null_resource.upload_source, aws_iam_role_policy.codebuild]
}
