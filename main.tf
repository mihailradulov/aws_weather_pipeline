terraform {
  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ------------------------------------------------------------------------------
# 1. Archive Lambda Source Code
# ------------------------------------------------------------------------------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_function.zip"
}

# ------------------------------------------------------------------------------
# 2. S3 Bucket for Weather Data Storage
# ------------------------------------------------------------------------------
resource "aws_s3_bucket" "weather_storage" {
  bucket        = var.s3_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "weather_storage_block" {
  bucket = aws_s3_bucket.weather_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ------------------------------------------------------------------------------
# 3. IAM Role & Policies for Lambda
# ------------------------------------------------------------------------------
resource "aws_iam_role" "lambda_exec_role" {
  name = "get_weather_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Policy for S3 Write Access
resource "aws_iam_policy" "lambda_s3_policy" {
  name        = "get_weather_s3_policy"
  description = "Allows get_weather Lambda to write JSON payloads to S3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl"
        ]
        Resource = "${aws_s3_bucket.weather_storage.arn}/*"
      }
    ]
  })
}

# Attach AWS Managed Policy for CloudWatch Logging
resource "aws_iam_role_policy_attachment" "lambda_logs_attachment" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Attach Custom S3 Policy
resource "aws_iam_role_policy_attachment" "lambda_s3_attachment" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.lambda_s3_policy.arn
}

# ------------------------------------------------------------------------------
# 4. CloudWatch Log Group for Lambda
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/get_weather"
  retention_in_days = 14
}

# ------------------------------------------------------------------------------
# 5. AWS Lambda Function
# ------------------------------------------------------------------------------
resource "aws_lambda_function" "get_weather" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "get_weather"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      OPENWEATHER_API_KEY = var.openweather_api_key
      S3_BUCKET_NAME      = aws_s3_bucket.weather_storage.id
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_log_group,
    aws_iam_role_policy_attachment.lambda_logs_attachment,
    aws_iam_role_policy_attachment.lambda_s3_attachment
  ]
}

# ------------------------------------------------------------------------------
# 6. EventBridge (CloudWatch Events) Schedule Trigger
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "weather_schedule" {
  name                = "get-weather-30min-schedule"
  description         = "Triggers get_weather Lambda at :05 and :35 past every hour"
  schedule_expression = "cron(5,35 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.weather_schedule.name
  target_id = "TriggerGetWeatherLambda"
  arn       = aws_lambda_function.get_weather.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_weather.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weather_schedule.arn
}