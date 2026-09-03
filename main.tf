terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# ==========================================
# 1. S3 BUCKETS (Bronze & Silver)
# ==========================================

resource "aws_s3_bucket" "bronze_bucket" {
  bucket        = "s3-weather-data-lake-${data.aws_caller_identity.current.account_id}-bronze"
  force_destroy = true
}

resource "aws_s3_bucket" "silver_bucket" {
  bucket        = "s3-weather-data-lake-${data.aws_caller_identity.current.account_id}-silver"
  force_destroy = true
}

# ==========================================
# 2. IAM ROLE & POLICIES FOR LAMBDAS
# ==========================================

resource "aws_iam_role" "lambda_exec_role" {
  name = "weather_data_lake_lambda_role_${var.environment}"

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

# Basic Execution Policy (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Policy for S3 Access (Bronze & Silver)
resource "aws_iam_policy" "s3_access_policy" {
  name        = "weather_data_lake_s3_policy_${var.environment}"
  description = "IAM policy for accessing Bronze and Silver S3 buckets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.bronze_bucket.arn,
          "${aws_s3_bucket.bronze_bucket.arn}/*",
          aws_s3_bucket.silver_bucket.arn,
          "${aws_s3_bucket.silver_bucket.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.s3_access_policy.arn
}

# ==========================================
# 3. LAMBDA 1: BRONZE (lambda_bronze_get_weather)
# ==========================================

data "archive_file" "bronze_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/bronze"
  output_path = "${path.module}/build/bronze.zip"
}

resource "aws_lambda_function" "bronze_lambda" {
  filename         = data.archive_file.bronze_zip.output_path
  source_code_hash = data.archive_file.bronze_zip.output_base64sha256
  function_name    = "lambda_bronze_get_weather"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "lambda_bronze_get_weather.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      BRONZE_BUCKET_NAME  = aws_s3_bucket.bronze_bucket.bucket
      OPENWEATHER_API_KEY = var.openweather_api_key
    }
  }
}

# EventBridge Rule (Schedule Trigger - Hourly)
resource "aws_cloudwatch_event_rule" "hourly_schedule" {
  name                = "weather_fetch_hourly_rule"
  schedule_expression = "cron(5,35 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "trigger_bronze_lambda" {
  rule      = aws_cloudwatch_event_rule.hourly_schedule.name
  target_id = "TriggerBronzeLambda"
  arn       = aws_lambda_function.bronze_lambda.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bronze_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hourly_schedule.arn
}

# Policy for Glue Catalog access (for Silver Lambda dynamic partition creation)
resource "aws_iam_policy" "glue_partition_policy" {
  name        = "weather_data_lake_glue_policy_${var.environment}"
  description = "IAM policy for creating dynamic partitions in Glue Data Catalog"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:CreatePartition"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_glue" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.glue_partition_policy.arn
}

# ==========================================
# 4. LAMBDA 2: SILVER (lambda_silver_generate_parquet)
# ==========================================

data "archive_file" "silver_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/silver"
  output_path = "${path.module}/build/silver.zip"
}

resource "aws_cloudwatch_log_group" "silver_lambda_logs" {
  name              = "/aws/lambda/lambda_silver_generate_parquet"
  retention_in_days = 7
}

resource "aws_lambda_function" "silver_lambda" {
  filename         = data.archive_file.silver_zip.output_path
  source_code_hash = data.archive_file.silver_zip.output_base64sha256
  function_name    = "lambda_silver_generate_parquet"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "lambda_silver_generate_parquet.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 512

  layers = [
    "arn:aws:lambda:${var.aws_region}:336392948345:layer:AWSSDKPandas-Python311:12"
  ]

  environment {
    variables = {
      SILVER_BUCKET_NAME = aws_s3_bucket.silver_bucket.bucket
      GLUE_DATABASE_NAME = aws_glue_catalog_database.weather_silver_db.name
      GLUE_TABLE_NAME    = aws_glue_catalog_table.weather_silver_table.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.silver_lambda_logs
  ]
}

# ==========================================
# 5. S3 EVENTBRIDGE NOTIFICATION & TRIGGER
# ==========================================

# Activating EventBridge notifications for Bronze bucket
resource "aws_s3_bucket_notification" "bronze_file_notification" {
  bucket      = aws_s3_bucket.bronze_bucket.id
  eventbridge = true
}

# EventBridge Rule: Catch 'Object Created' events from Bronze bucket
resource "aws_cloudwatch_event_rule" "s3_bronze_object_created" {
  name        = "s3_bronze_object_created_rule"
  description = "Trigger Silver Lambda on new S3 object in Bronze bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.bronze_bucket.bucket]
      }
    }
  })
}

# EventBridge Target: Triggers Silver Lambda
resource "aws_cloudwatch_event_target" "trigger_silver_lambda" {
  rule      = aws_cloudwatch_event_rule.s3_bronze_object_created.name
  target_id = "TriggerSilverLambda"
  arn       = aws_lambda_function.silver_lambda.arn
}

# Permission for EventBridge to invoke Silver Lambda
resource "aws_lambda_permission" "allow_eventbridge_to_trigger_silver" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.silver_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.s3_bronze_object_created.arn
}

# ==========================================
# 6. AWS GLUE DATA CATALOG & ATHENA
# ==========================================

# Glue Database for Silver layer
resource "aws_glue_catalog_database" "weather_silver_db" {
  name        = "weather_silver_db_${var.environment}"
  description = "Glue Catalog Database for Silver Parquet Weather Data"
}

# Glue Crawler for automatic schema and partition discovery in Silver S3
resource "aws_glue_crawler" "silver_weather_crawler" {
  database_name = aws_glue_catalog_database.weather_silver_db.name
  name          = "weather_silver_crawler_${var.environment}"
  role          = aws_iam_role.glue_crawler_role.arn

  s3_target {
    path = "s3://${aws_s3_bucket.silver_bucket.bucket}/"
  }

  # Refreshing the catalog on changes
  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }
}

# Glue Table definition for Silver Parquet weather data
resource "aws_glue_catalog_table" "weather_silver_table" {
  name          = "s3_weather_silver"
  database_name = aws_glue_catalog_database.weather_silver_db.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"        = "parquet"
    "typeOfData"            = "file"
    "parquet.compression"   = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.silver_bucket.bucket}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                   = "ParquetHiveSerDe"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "city_id"
      type = "bigint"
    }
    columns {
      name = "city_name"
      type = "string"
    }
    columns {
      name = "country"
      type = "string"
    }
    columns {
      name = "coord_lon"
      type = "double"
    }
    columns {
      name = "coord_lat"
      type = "double"
    }
    columns {
      name = "weather_id"
      type = "bigint"
    }
    columns {
      name = "weather_main"
      type = "string"
    }
    columns {
      name = "weather_description"
      type = "string"
    }
    columns {
      name = "weather_icon"
      type = "string"
    }
    columns {
      name = "base_station"
      type = "string"
    }
    columns {
      name = "temp"
      type = "double"
    }
    columns {
      name = "feels_like"
      type = "double"
    }
    columns {
      name = "temp_min"
      type = "double"
    }
    columns {
      name = "temp_max"
      type = "double"
    }
    columns {
      name = "pressure"
      type = "bigint"
    }
    columns {
      name = "sea_level_pressure"
      type = "bigint"
    }
    columns {
      name = "ground_level_pressure"
      type = "bigint"
    }
    columns {
      name = "humidity"
      type = "bigint"
    }
    columns {
      name = "visibility"
      type = "bigint"
    }
    columns {
      name = "wind_speed"
      type = "double"
    }
    columns {
      name = "wind_deg"
      type = "double"
    }
    columns {
      name = "wind_gust"
      type = "double"
    }
    columns {
      name = "clouds_all"
      type = "double"
    }
    columns {
      name = "rain_1h"
      type = "double"
    }
    columns {
      name = "rain_3h"
      type = "double"
    }
    columns {
      name = "snow_1h"
      type = "double"
    }
    columns {
      name = "snow_3h"
      type = "double"
    }
    columns {
      name = "sys_type"
      type = "bigint"
    }
    columns {
      name = "sys_id"
      type = "bigint"
    }
    columns {
      name = "sys_message"
      type = "double"
    }
    columns {
      name = "timezone_offset"
      type = "bigint"
    }
    columns {
      name = "dt_utc"
      type = "string"
    }
    columns {
      name = "dt_local"
      type = "string"
    }
    columns {
      name = "sunrise_utc"
      type = "string"
    }
    columns {
      name = "sunrise_local"
      type = "string"
    }
    columns {
      name = "sunset_utc"
      type = "string"
    }
    columns {
      name = "sunset_local"
      type = "string"
    }
    columns {
      name = "http_status_code"
      type = "bigint"
    }
    columns {
      name = "ingested_at_utc"
      type = "string"
    }
    columns {
      name = "ingested_at_local"
      type = "string"
    }
    columns {
      name = "source_raw_file"
      type = "string"
    }
  }

  partition_keys {
    name = "partition_date"
    type = "string"
  }
}

# IAM Role for Glue Crawler
resource "aws_iam_role" "glue_crawler_role" {
  name = "weather_glue_crawler_role_${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service_attachment" {
  role       = aws_iam_role.glue_crawler_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy_attachment" "glue_s3_attachment" {
  role       = aws_iam_role.glue_crawler_role.name
  policy_arn = aws_iam_policy.s3_access_policy.arn
}

# S3 Bucket for Athena query results
resource "aws_s3_bucket" "athena_results_bucket" {
  bucket        = "s3-athena-results-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

# Athena Workgroup
resource "aws_athena_workgroup" "weather_athena_workgroup" {
  name = "weather_analytics_workgroup"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results_bucket.bucket}/queries/"
    }
  }
}

# ==========================================
# 7. CLOUDWATCH ALARMS FOR MONITORING
# ==========================================

# Alarm for errors in Silver Lambda
resource "aws_cloudwatch_metric_alarm" "silver_lambda_error_alarm" {
  alarm_name          = "silver_lambda_execution_errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "This alarm is triggered when at least 1 error occurs in the Silver Lambda function within a 5-minute period."

  dimensions = {
    FunctionName = aws_lambda_function.silver_lambda.function_name
  }
}