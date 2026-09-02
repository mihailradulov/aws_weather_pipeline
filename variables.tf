variable "aws_region" {
  type        = string
  description = "AWS region where resources will be deployed"
  default     = "eu-north-1"
}

variable "s3_bucket_name" {
  type        = string
  description = "Unique name for the S3 bucket storing weather payloads"
}

variable "openweather_api_key" {
  type        = string
  description = "API key for OpenWeatherMap"
  sensitive   = true
}