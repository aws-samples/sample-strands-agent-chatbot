# AgentCore Registry — Custom Resource via Lambda + CloudFormation stack.
#
# AgentCore Registry / RegistryRecord have no Terraform provider. We wrap the
# boto3 API in a Lambda, invoke it as a CFN Custom Resource, and let CFN drive
# Create/Update/Delete lifecycle. Definitions live in YAML under
# infra/registry/definitions/ and are split into mcp/, a2a/, and skills/.
#
# Records are batched into 3 stacks by type (mcp, a2a, skills) to minimize
# CloudFormation stack count while keeping type-level isolation.

data "aws_caller_identity" "current" {}

locals {
  registry_name    = "${var.project_name}-${var.environment}-registry"
  function_name    = "${var.project_name}-${var.environment}-registry-manager"
  definitions_root = "${path.module}/../../registry/definitions"
  skills_md_root   = "${var.repo_root}/chatbot-app/agentcore/skills"

  # -- MCP defs --------------------------------------------------------------
  _mcp_files = fileset("${local.definitions_root}/mcp", "*.yaml")
  mcp_defs = {
    for f in local._mcp_files :
    trimsuffix(f, ".yaml") => merge(
      yamldecode(file("${local.definitions_root}/mcp/${f}")),
      { descriptor_type = "MCP" }
    )
  }

  # -- A2A defs --------------------------------------------------------------
  _a2a_files = fileset("${local.definitions_root}/a2a", "*.yaml")
  a2a_defs = {
    for f in local._a2a_files :
    trimsuffix(f, ".yaml") => merge(
      yamldecode(file("${local.definitions_root}/a2a/${f}")),
      { descriptor_type = "A2A" }
    )
  }

  # -- AGENT_SKILLS defs -----------------------------------------------------
  _skill_files = fileset("${local.definitions_root}/skills", "*.yaml")
  skill_defs = {
    for f in local._skill_files :
    trimsuffix(f, ".yaml") => merge(
      yamldecode(file("${local.definitions_root}/skills/${f}")),
      {
        descriptor_type = "AGENT_SKILLS"
        skill_md        = file("${local.skills_md_root}/${yamldecode(file("${local.definitions_root}/skills/${f}")).skill_md_path}")
      }
    )
  }

  # Only AGENT_SKILLS records are registered. Endpoint URLs are embedded
  # directly in each skill's _meta.endpointUrl instead of separate MCP/A2A
  # records — avoids sync auth issues (Gateway uses CUSTOM_JWT, not IAM).
  _skill_records = local.skill_defs

  # A2A skills also get AGENT_SKILLS records (with SKILL.md from skill_defs
  # that share the same basename). Merge A2A metadata into the skill record.
  _a2a_skill_overrides = {
    for k, v in local.a2a_defs : k => v
    if contains(keys(local.skill_defs), k)
  }

  records_to_register = {
    for k, v in local._skill_records : k => v
    if length(var.enabled_components) == 0 || contains(var.enabled_components, k)
  }

  skill_records = local.records_to_register

  # Resolve endpoint URL per skill based on source type
  _skill_endpoint_url = {
    for k, v in local.records_to_register : k => (
      lookup(v, "source", "builtin") == "gateway" ? var.gateway_url :
      lookup(v, "source", "builtin") == "mcp" ? var.mcp_runtime_url :
      lookup(v, "source", "builtin") == "a2a" ? lookup(var.a2a_runtime_urls, k, "") :
      ""
    )
  }
}

# ============================================================
# Registry Manager Lambda
# ============================================================

resource "aws_iam_role" "registry_manager" {
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

resource "aws_iam_role_policy" "registry_manager" {
  name = "registry-manager-policy"
  role = aws_iam_role.registry_manager.id

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
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateRegistry",
          "bedrock-agentcore:GetRegistry",
          "bedrock-agentcore:UpdateRegistry",
          "bedrock-agentcore:DeleteRegistry",
          "bedrock-agentcore:ListRegistries",
          "bedrock-agentcore:CreateRegistryRecord",
          "bedrock-agentcore:GetRegistryRecord",
          "bedrock-agentcore:UpdateRegistryRecord",
          "bedrock-agentcore:DeleteRegistryRecord",
          "bedrock-agentcore:ListRegistryRecords",
          "bedrock-agentcore:SubmitRegistryRecordForApproval",
          "bedrock-agentcore:UpdateRegistryRecordStatus",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateWorkloadIdentity",
          "bedrock-agentcore:GetWorkloadIdentity",
          "bedrock-agentcore:UpdateWorkloadIdentity",
          "bedrock-agentcore:DeleteWorkloadIdentity",
          "bedrock-agentcore:ListWorkloadIdentities",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_function" "registry_manager" {
  function_name    = local.function_name
  filename         = "${path.module}/lambda/registry-manager.zip"
  source_code_hash = filemd5("${path.module}/lambda/registry-manager.zip")
  handler          = "index.lambda_handler"
  runtime          = "python3.14"
  timeout          = 300
  memory_size      = 256
  role             = aws_iam_role.registry_manager.arn

  tags = {
    Component = "registry"
  }
}

# ============================================================
# Registry (Custom Resource)
# ============================================================

resource "aws_cloudformation_stack" "registry" {
  name = "${var.project_name}-${var.environment}-registry"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = {
      Registry = {
        Type = "Custom::AgentCoreRegistry"
        Properties = {
          ServiceToken        = aws_lambda_function.registry_manager.arn
          Action              = "MANAGE_REGISTRY"
          RegistryName        = local.registry_name
          RegistryDescription = "Central catalog for ${var.project_name} (${var.environment})"
          AutoApproval        = "true"
        }
      }
    }
    Outputs = {
      RegistryId = {
        Value = { "Fn::GetAtt" = ["Registry", "RegistryId"] }
      }
    }
  })

  depends_on = [aws_lambda_function.registry_manager]
}

# ============================================================
# Registry Records — batched by type (3 stacks total)
# ============================================================

# Helper: build CFN resource properties for a single record
locals {
  _record_properties = {
    for k, v in local.records_to_register : k => {
      ServiceToken      = aws_lambda_function.registry_manager.arn
      Action            = "MANAGE_RECORD"
      RegistryId        = aws_cloudformation_stack.registry.outputs["RegistryId"]
      RecordName        = k
      RecordDescription = v.description
      RecordVersion     = "1.0.0"
      DescriptorType    = "AGENT_SKILLS"
      SkillMdContent    = v.skill_md
      SkillDefinitionJson = jsonencode({
        _meta = {
          source       = lookup(v, "source", "builtin")
          sourceRecord = lookup(v, "sourceRecord", null)
          tools        = lookup(v, "tools", [])
          endpointUrl  = lookup(local._skill_endpoint_url, k, "")
        }
      })
    }
  }
}

# --- Skills Records Stacks (one stack per record) ---
#
# CFN is used only for lifecycle (create/delete) — content updates go through
# null_resource.record_update below via direct Lambda invoke. This is because
# the CloudFormation get-template API strips non-ASCII characters to '?',
# which corrupts the Terraform state representation and produces a permanent
# drift loop on any skill_md containing em-dashes, smart quotes, etc.
# AgentCore Registry itself stores the content correctly; only CFN's
# surface representation was mangled.
resource "aws_cloudformation_stack" "records_skills" {
  for_each = local.skill_records
  name     = "${var.project_name}-${var.environment}-record-${each.key}"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = {
      Record = {
        Type       = "Custom::AgentCoreRegistryRecord"
        Properties = local._record_properties[each.key]
      }
    }
    Outputs = {
      RecordId = {
        Value = { "Fn::GetAtt" = ["Record", "RecordId"] }
      }
    }
  })

  lifecycle {
    # Suppress the CFN get-template non-ASCII corruption drift. Content
    # stays in sync via null_resource.record_update below.
    ignore_changes = [template_body]
  }

  depends_on = [aws_cloudformation_stack.registry]
}

# --- Record content sync (direct Lambda invoke on SKILL.md change) ---
#
# Whenever any input to the record content changes (skill_md body, definition
# JSON, description, endpoint URL), invoke the registry_manager Lambda in
# direct mode to update the live AgentCore record. This preserves correct
# UTF-8 and side-steps the CFN get-template corruption entirely.
resource "local_file" "record_update_payload" {
  for_each = local.skill_records
  # Write payload to /tmp to avoid state bloat; filename changes with content.
  filename = "/tmp/registry-payload-${var.project_name}-${var.environment}-${each.key}.json"
  content = jsonencode({
    direct_invoke         = true
    action                = "UPDATE_RECORD"
    registry_id           = aws_cloudformation_stack.registry.outputs["RegistryId"]
    record_id             = aws_cloudformation_stack.records_skills[each.key].outputs["RecordId"]
    description           = each.value.description
    record_version        = local._record_properties[each.key].RecordVersion
    skill_md_content      = each.value.skill_md
    skill_definition_json = local._record_properties[each.key].SkillDefinitionJson
  })
}

resource "null_resource" "record_update" {
  for_each = local.skill_records

  triggers = {
    payload_sha = sha256(local_file.record_update_payload[each.key].content)
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      aws lambda invoke \
        --function-name "${aws_lambda_function.registry_manager.function_name}" \
        --region "${var.aws_region}" \
        --cli-binary-format raw-in-base64-out \
        --payload "fileb://${local_file.record_update_payload[each.key].filename}" \
        /tmp/registry-update-${each.key}.out.json >/dev/null
      # Surface Lambda errors (FunctionError field present on failure).
      if grep -q '"errorMessage"' /tmp/registry-update-${each.key}.out.json 2>/dev/null; then
        echo "Lambda error for record ${each.key}:" >&2
        cat /tmp/registry-update-${each.key}.out.json >&2
        exit 1
      fi
    EOT
  }

  depends_on = [aws_cloudformation_stack.records_skills, local_file.record_update_payload]
}

# ============================================================
# SSM
# ============================================================

resource "aws_ssm_parameter" "registry_id" {
  name  = "/${var.project_name}/${var.environment}/registry/registry-id"
  type  = "String"
  value = aws_cloudformation_stack.registry.outputs["RegistryId"]
}
