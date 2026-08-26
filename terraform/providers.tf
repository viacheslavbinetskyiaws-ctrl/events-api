provider "aws" {
  region                      = "eu-central-1"
  access_key                  = var.use_locastack ? "test" : null
  secret_key                  = var.use_locastack ? "test" : null
  skip_credentials_validation = var.use_locastack
  skip_metadata_api_check     = var.use_locastack
  skip_region_validation      = var.use_locastack
  skip_requesting_account_id  = var.use_locastack
  s3_use_path_style           = var.use_locastack

  dynamic "endpoints" {
    for_each = var.use_locastack ? [1] : []

    content {
      s3  = "http://localhost:4566"
      ec2 = "http://localhost:4566"
      iam = "http://localhost:4566"
    }
  }
}
