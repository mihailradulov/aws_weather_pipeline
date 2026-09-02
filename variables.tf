variable "aws_region" {
  type        = string
  default     = "eu-north-1"
  description = "AWS resource region"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment (dev, stage, prod)"
}

variable "openweather_api_key" {
  type        = string
  sensitive   = true
  description = "API Key for OpenWeather Service"
}