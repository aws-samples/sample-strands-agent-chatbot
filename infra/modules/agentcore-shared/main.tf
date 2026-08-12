locals {
  name_prefix = replace("${var.project_name}_${var.environment}", "-", "_")
  code_interpreter_config_suffix = substr(sha1(jsonencode({
    execution_role_arn = var.code_interpreter_execution_role_arn
    subnet_ids         = sort(var.code_interpreter_subnet_ids)
    security_group_ids = sort(var.code_interpreter_security_group_ids)
  })), 0, 8)
}

# Dedicated role the Browser assumes for navigation logging / bot-auth.
# Kept inside this module to avoid a cycle with the consuming runtimes.
resource "aws_iam_role" "browser" {
  name = "${var.project_name}-${var.environment}-browser-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "browser" {
  name = "browser-policy"
  role = aws_iam_role.browser.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:*"
      },
    ]
  })
}

resource "aws_bedrockagentcore_code_interpreter" "this" {
  name               = "${local.name_prefix}_ci_${local.code_interpreter_config_suffix}"
  description        = "Shared Code Interpreter for ${var.project_name} ${var.environment}"
  execution_role_arn = var.code_interpreter_execution_role_arn

  network_configuration {
    network_mode = "VPC"

    vpc_config {
      subnets         = var.code_interpreter_subnet_ids
      security_groups = var.code_interpreter_security_group_ids
    }
  }
}

resource "aws_bedrockagentcore_browser" "this" {
  name               = "${local.name_prefix}_browser_v2"
  description        = "Shared AgentCore Browser for web automation"
  execution_role_arn = aws_iam_role.browser.arn

  network_configuration {
    network_mode = "PUBLIC"
  }
}

resource "aws_ssm_parameter" "code_interpreter_id" {
  name  = "/${var.project_name}/${var.environment}/agentcore/code-interpreter-id"
  type  = "String"
  value = aws_bedrockagentcore_code_interpreter.this.code_interpreter_id
}

resource "aws_ssm_parameter" "browser_id" {
  name  = "/${var.project_name}/${var.environment}/agentcore/browser-id"
  type  = "String"
  value = aws_bedrockagentcore_browser.this.browser_id
}

# Nova Act Workflow Definition — preview service with no Terraform provider.
# Creates one via CLI/boto3 if a name wasn't supplied. The workflow is a
# one-time, account/region-scoped IAM auth handle for the Nova Act browser.
locals {
  nova_act_workflow_name_effective = (
    var.nova_act_workflow_name != ""
    ? var.nova_act_workflow_name
    : "${replace(var.project_name, "-", "_")}_${var.environment}_workflow"
  )
}

resource "null_resource" "nova_act_workflow" {
  triggers = {
    name   = local.nova_act_workflow_name_effective
    region = "us-east-1"
    # Scope auto-cleanup to workflows this module created. Workflows supplied
    # externally (nova_act_workflow_name != "") are treated as user-owned and
    # left alone on destroy.
    managed = var.nova_act_workflow_name == "" ? "true" : "false"
  }

  # Idempotent create: deploy.sh already resolved/created the workflow before
  # terraform runs. We re-verify here using the isolated deploy venv python
  # ($DEPLOY_VENV_PYTHON) to avoid depending on the user's system boto3.
  provisioner "local-exec" {
    command = <<-EOT
      set -e
      NAME="${self.triggers.name}"
      REGION="${self.triggers.region}"
      PYBIN="$${DEPLOY_VENV_PYTHON:-python3}"

      "$PYBIN" <<PY || echo "WARNING: Could not verify/create Nova Act workflow '$NAME'. Create it manually via boto3 nova-act create_workflow_definition."
      import boto3, sys
      try:
          c = boto3.client("nova-act", region_name="$REGION")
      except Exception as e:
          print(f"boto3 nova-act client unavailable: {e}", file=sys.stderr); sys.exit(2)
      try:
          c.create_workflow_definition(name="$NAME")
          print("Created Nova Act workflow '$NAME'")
      except c.exceptions.ConflictException:
          print("Nova Act workflow '$NAME' already exists")
      PY
    EOT
  }

  # Destroy: delete only if we created it (managed == "true").
  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command    = <<-EOT
      set +e
      [ "${self.triggers.managed}" = "true" ] || { echo "Skipping workflow delete (externally supplied)"; exit 0; }
      NAME="${self.triggers.name}"
      REGION="${self.triggers.region}"
      PYBIN="$${DEPLOY_VENV_PYTHON:-python3}"

      "$PYBIN" <<PY
      import boto3, sys
      try:
          c = boto3.client("nova-act", region_name="$REGION")
          c.delete_workflow_definition(name="$NAME")
          print("Deleted Nova Act workflow '$NAME'")
      except Exception as e:
          print(f"Nova Act workflow '$NAME' not deleted: {e}", file=sys.stderr)
      PY
    EOT
  }
}

resource "aws_ssm_parameter" "nova_act_workflow_name" {
  name  = "/${var.project_name}/${var.environment}/agentcore/nova-act-workflow-name"
  type  = "String"
  value = local.nova_act_workflow_name_effective

  depends_on = [null_resource.nova_act_workflow]
}
