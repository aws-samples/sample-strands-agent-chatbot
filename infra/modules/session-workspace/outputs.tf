output "file_system_id" {
  value = aws_s3files_file_system.this.id
}

output "file_system_arn" {
  value = aws_s3files_file_system.this.arn
}

output "frontend_access_point_arn" {
  value = aws_s3files_access_point.frontend.arn
}

output "mount_target_security_group_id" {
  value = aws_security_group.mount_target.id
}

output "code_interpreter_execution_role_arn" {
  value = aws_iam_role.code_interpreter.arn
}

output "code_interpreter_subnet_ids" {
  value = sort([for subnet in aws_subnet.code_interpreter_private : subnet.id])
}

output "code_interpreter_nat_gateway_id" {
  value = try(aws_nat_gateway.code_interpreter[0].id, "")
}
