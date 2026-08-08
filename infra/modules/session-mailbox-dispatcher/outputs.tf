output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "worker_function_name" {
  value = aws_lambda_function.worker.function_name
}

output "wake_queue_url" {
  value = aws_sqs_queue.wake.url
}

output "wake_dead_letter_queue_url" {
  value = aws_sqs_queue.wake_dead_letter.url
}
