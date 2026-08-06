# terraform {
#   backend "s3" {
#     bucket                      = "events-api-tfstate"
#     key                         = "events-api/terraform.tfstate"
#     region                      = "us-east-1"
#     use_lockfile                = true
#     access_key                  = "test"
#     secret_key                  = "test"
#     skip_credentials_validation = true
#     skip_metadata_api_check     = true
#     skip_region_validation      = true
#     skip_requesting_account_id  = true
#     use_path_style              = true
#     endpoints = {
#       s3 = "http://localhost:4566"
#     }
#   }
# }
