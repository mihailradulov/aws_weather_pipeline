output "s3_bucket_name" {
  description = "Name of the S3 bucket storing weather payloads"
  value       = aws_s3_bucket.weather_storage.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.weather_storage.arn
}

output "lambda_function_name" {
  description = "Name of the deployed Lambda function"
  value       = aws_lambda_function.get_weather.function_name
}

output "lambda_function_arn" {
  description = "ARN of the deployed Lambda function"
  value       = aws_lambda_function.get_weather.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch Log Group path for Lambda logs"
  value       = aws_cloudwatch_log_group.lambda_log_group.name
}