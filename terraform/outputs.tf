output "state_bucket_id" {
  description = "Name of the real S3 bucket holding Terraform state, once created"
  value       = module.s3.bucket_id
}
