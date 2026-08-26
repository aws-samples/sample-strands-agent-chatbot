output "users_table_name" {
  value = aws_dynamodb_table.users.name
}

output "users_table_arn" {
  value = aws_dynamodb_table.users.arn
}

output "sessions_table_name" {
  value = aws_dynamodb_table.sessions.name
}

output "sessions_table_arn" {
  value = aws_dynamodb_table.sessions.arn
}

output "session_orchestration_table_name" {
  value = aws_dynamodb_table.session_orchestration.name
}

output "session_orchestration_table_arn" {
  value = aws_dynamodb_table.session_orchestration.arn
}

output "session_orchestration_stream_arn" {
  value = aws_dynamodb_table.session_orchestration.stream_arn
}

output "session_files_table_name" {
  value = aws_dynamodb_table.session_files.name
}

output "session_files_table_arn" {
  value = aws_dynamodb_table.session_files.arn
}
