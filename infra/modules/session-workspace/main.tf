locals {
  prefix = "${var.project_name}-${var.environment}-workspace"
}

data "aws_vpc" "this" {
  id = var.vpc_id
}

data "aws_subnet" "existing" {
  for_each = toset(var.subnet_ids)
  id       = each.value
}

locals {
  code_interpreter_nat_enabled = length(var.code_interpreter_private_subnets) > 0
  public_subnet_by_az = {
    for subnet_id, subnet in data.aws_subnet.existing :
    subnet.availability_zone_id => subnet_id
    if contains(keys(var.code_interpreter_private_subnets), subnet.availability_zone_id)
  }
  nat_availability_zone_id = local.code_interpreter_nat_enabled ? sort(keys(var.code_interpreter_private_subnets))[0] : ""
  nat_public_subnet_id = (
    local.code_interpreter_nat_enabled
    ? lookup(local.public_subnet_by_az, local.nat_availability_zone_id, "")
    : ""
  )
}

resource "aws_eip" "code_interpreter_nat" {
  count  = local.code_interpreter_nat_enabled ? 1 : 0
  domain = "vpc"

  tags = {
    Name        = "${local.prefix}-code-interpreter-nat"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_nat_gateway" "code_interpreter" {
  count         = local.code_interpreter_nat_enabled ? 1 : 0
  allocation_id = aws_eip.code_interpreter_nat[0].id
  subnet_id     = local.nat_public_subnet_id

  tags = {
    Name        = "${local.prefix}-code-interpreter"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  lifecycle {
    precondition {
      condition     = local.nat_public_subnet_id != ""
      error_message = "A public subnet is required in the first configured Code Interpreter availability zone."
    }
  }
}

resource "aws_subnet" "code_interpreter_private" {
  for_each = var.code_interpreter_private_subnets

  vpc_id                  = var.vpc_id
  availability_zone_id    = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false

  tags = {
    Name        = "${local.prefix}-code-interpreter-${each.key}"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_route_table" "code_interpreter_private" {
  count  = local.code_interpreter_nat_enabled ? 1 : 0
  vpc_id = var.vpc_id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.code_interpreter[0].id
  }

  tags = {
    Name        = "${local.prefix}-code-interpreter-private"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_route_table_association" "code_interpreter_private" {
  for_each = aws_subnet.code_interpreter_private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.code_interpreter_private[0].id
}

resource "aws_iam_role" "s3files" {
  name = "${local.prefix}-s3files"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3FilesAssumeRole"
      Effect    = "Allow"
      Principal = { Service = "elasticfilesystem.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:s3files:${var.aws_region}:${var.account_id}:file-system/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "s3files" {
  name = "backing-bucket"
  role = aws_iam_role.s3files.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BucketPermissions"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:ListBucketVersions",
        ]
        Resource = var.artifact_bucket_arn
        Condition = {
          StringEquals = {
            "aws:ResourceAccount" = var.account_id
          }
        }
      },
      {
        Sid    = "S3ObjectPermissions"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:DeleteObject*",
          "s3:GetObject*",
          "s3:List*",
          "s3:PutObject*",
        ]
        Resource = "${var.artifact_bucket_arn}/*"
        Condition = {
          StringEquals = {
            "aws:ResourceAccount" = var.account_id
          }
        }
      },
      {
        Sid    = "EventBridgeManage"
        Effect = "Allow"
        Action = [
          "events:DeleteRule",
          "events:DisableRule",
          "events:EnableRule",
          "events:PutRule",
          "events:PutTargets",
          "events:RemoveTargets",
        ]
        Resource = "arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"
        Condition = {
          StringEquals = {
            "events:ManagedBy" = "elasticfilesystem.amazonaws.com"
          }
        }
      },
      {
        Sid    = "EventBridgeRead"
        Effect = "Allow"
        Action = [
          "events:DescribeRule",
          "events:ListRuleNamesByTarget",
          "events:ListRules",
          "events:ListTargetsByRule",
        ]
        Resource = "arn:aws:events:*:*:rule/*"
      },
    ]
  })
}

resource "aws_s3files_file_system" "this" {
  bucket                = var.artifact_bucket_arn
  role_arn              = aws_iam_role.s3files.arn
  accept_bucket_warning = true

  tags = {
    Name        = local.prefix
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# S3 objects written by the upload path are imported when the mounted
# directory is first accessed. Files written through the mount are exported
# back to the same bucket automatically by S3 Files.
resource "aws_s3files_synchronization_configuration" "this" {
  file_system_id = aws_s3files_file_system.this.id

  # S3 Files requires a catch-all rule rooted at the empty prefix. Keep its
  # import threshold minimal so unrelated artifact namespaces are not staged
  # into the filesystem; the workspace-specific rule below takes precedence.
  import_data_rule {
    prefix         = ""
    size_less_than = 1
    trigger        = "ON_DIRECTORY_FIRST_ACCESS"
  }

  import_data_rule {
    prefix         = "code-interpreter-workspace/"
    size_less_than = 5368709120
    trigger        = "ON_DIRECTORY_FIRST_ACCESS"
  }

  # Expiration removes cold data from the filesystem cache only after it has
  # synchronized to S3. A later access imports the durable object again.
  expiration_data_rule {
    days_after_last_access = 30
  }
}

resource "aws_security_group" "mount_target" {
  name        = "${local.prefix}-mount"
  description = "NFS access to the session workspace S3 Files mount targets"
  vpc_id      = var.vpc_id

  ingress {
    description = "NFS from workspace clients in the VPC"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.this.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_s3files_mount_target" "this" {
  for_each = toset(var.subnet_ids)

  file_system_id = aws_s3files_file_system.this.id
  subnet_id      = each.value
  security_groups = [
    aws_security_group.mount_target.id,
  ]
}

# The frontend is trusted and enforces user/session authorization in the API.
# It mounts only the Code Interpreter namespace and never executes user code.
resource "aws_s3files_access_point" "frontend" {
  file_system_id = aws_s3files_file_system.this.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/code-interpreter-workspace"

    creation_permissions {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }

  tags = {
    Name        = "${local.prefix}-frontend"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [aws_s3files_mount_target.this]
}

resource "aws_iam_role" "code_interpreter" {
  name = "${local.prefix}-code-interpreter"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "code_interpreter" {
  name = "session-workspace"
  role = aws_iam_role.code_interpreter.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "MountSessionAccessPoints"
        Effect = "Allow"
        Action = [
          "s3files:ClientMount",
          "s3files:ClientWrite",
          "s3files:GetAccessPoint",
        ]
        Resource = aws_s3files_file_system.this.arn
        Condition = {
          ArnLike = {
            "s3files:AccessPointArn" = "${aws_s3files_file_system.this.arn}/access-point/*"
          }
        }
      },
      {
        Sid    = "ReadWorkspaceObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
        ]
        Resource = "${var.artifact_bucket_arn}/code-interpreter-workspace/*"
      },
      {
        Sid      = "ListWorkspaceObjects"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = var.artifact_bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "code-interpreter-workspace",
              "code-interpreter-workspace/*",
            ]
          }
        }
      },
    ]
  })
}
