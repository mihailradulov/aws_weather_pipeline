output "bronze_bucket_name" {
  value       = aws_s3_bucket.bronze_bucket.bucket
  description = "Име на Bronze S3 бъкета"
}

output "silver_bucket_name" {
  value       = aws_s3_bucket.silver_bucket.bucket
  description = "Име на Silver S3 бъкета"
}

output "bronze_lambda_arn" {
  value       = aws_lambda_function.bronze_lambda.arn
  description = "ARN на Bronze Lambda функцията"
}

output "silver_lambda_arn" {
  value       = aws_lambda_function.silver_lambda.arn
  description = "ARN на Silver Lambda функцията"
}