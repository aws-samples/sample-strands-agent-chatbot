output "runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}

output "runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

# HTTPS URL suitable for use as a Gateway MCP server target.
output "runtime_invocation_url" {
  value = join("", [
    "https://bedrock-agentcore.",
    var.aws_region,
    ".amazonaws.com/runtimes/",
    urlencode(aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn),
    "/invocations?qualifier=DEFAULT",
  ])
}

# Name of the workload identity AgentCore creates alongside the runtime.
# Needed to register OAuth 2.0 return URLs for 3LO. Empty until the runtime
# finishes provisioning.
output "workload_identity_name" {
  value = try(
    basename(aws_bedrockagentcore_agent_runtime.this.workload_identity_details[0].workload_identity_arn),
    ""
  )
}

output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "source_hash" {
  value = local.source_hash
}
