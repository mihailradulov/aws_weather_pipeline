# ==========================================
# S3 BUCKETS
# ==========================================

output "bronze_bucket_name" {
  description = "Name of the Bronze S3 Bucket"
  value       = aws_s3_bucket.bronze_bucket.bucket
}

output "bronze_bucket_arn" {
  description = "ARN of the Bronze S3 Bucket"
  value       = aws_s3_bucket.bronze_bucket.arn
}

output "silver_bucket_name" {
  description = "Name of the Silver S3 Bucket"
  value       = aws_s3_bucket.silver_bucket.bucket
}

output "silver_bucket_arn" {
  description = "ARN of the Silver S3 Bucket"
  value       = aws_s3_bucket.silver_bucket.arn
}

output "athena_results_bucket_name" {
  description = "Name of the Athena Query Results S3 Bucket"
  value       = aws_s3_bucket.athena_results_bucket.bucket
}

output "athena_results_bucket_arn" {
  description = "ARN of the Athena Query Results S3 Bucket"
  value       = aws_s3_bucket.athena_results_bucket.arn
}

# ==========================================
# LAMBDA FUNCTIONS
# ==========================================

output "bronze_lambda_function_name" {
  description = "Name of the Bronze layer ingestion Lambda function"
  value       = aws_lambda_function.bronze_lambda.function_name
}

output "bronze_lambda_function_arn" {
  description = "ARN of the Bronze layer ingestion Lambda function"
  value       = aws_lambda_function.bronze_lambda.arn
}

output "silver_lambda_function_name" {
  description = "Name of the Silver layer processing Lambda function"
  value       = aws_lambda_function.silver_lambda.function_name
}

output "silver_lambda_function_arn" {
  description = "ARN of the Silver layer processing Lambda function"
  value       = aws_lambda_function.silver_lambda.arn
}

# ==========================================
# GLUE & ATHENA RESOURCES
# ==========================================

output "glue_catalog_database_name" {
  description = "Name of the Glue Catalog Database"
  value       = aws_glue_catalog_database.weather_silver_db.name
}

output "glue_catalog_database_arn" {
  description = "ARN of the Glue Catalog Database"
  value       = aws_glue_catalog_database.weather_silver_db.arn
}

output "silver_crawler_name" {
  description = "Name of the Glue Crawler for Silver Layer Parquet Data"
  value       = aws_glue_crawler.silver_weather_crawler.name
}

output "silver_crawler_arn" {
  description = "ARN of the Glue Crawler for Silver Layer Parquet Data"
  value       = aws_glue_crawler.silver_weather_crawler.arn
}

output "athena_workgroup_name" {
  description = "Name of the Athena Workgroup"
  value       = aws_athena_workgroup.weather_athena_workgroup.name
}

output "athena_workgroup_arn" {
  description = "ARN of the Athena Workgroup"
  value       = aws_athena_workgroup.weather_athena_workgroup.arn
}

# ==========================================
# GLUE DATA CATALOG OUTPUTS
# ==========================================

output "glue_database_name" {
  description = "The name of the Glue Catalog Database"
  value       = aws_glue_catalog_database.weather_silver_db.name
}

output "glue_database_arn" {
  description = "The ARN of the Glue Catalog Database"
  value       = aws_glue_catalog_database.weather_silver_db.arn
}

output "glue_table_name" {
  description = "The name of the Glue Catalog Table for Silver weather data"
  value       = aws_glue_catalog_table.weather_silver_table.name
}

output "glue_table_arn" {
  description = "The ARN of the Glue Catalog Table for Silver weather data"
  value       = aws_glue_catalog_table.weather_silver_table.arn
}

# ==========================================
# MONITORING & ALARMS
# ==========================================

output "silver_lambda_error_alarm_name" {
  description = "Name of the CloudWatch Alarm for Silver Lambda errors"
  value       = aws_cloudwatch_metric_alarm.silver_lambda_error_alarm.alarm_name
}

output "silver_lambda_error_alarm_arn" {
  description = "ARN of the CloudWatch Alarm for Silver Lambda errors"
  value       = aws_cloudwatch_metric_alarm.silver_lambda_error_alarm.arn
}