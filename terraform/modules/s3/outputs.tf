output "bucket_id" {
  description = "Name of the S3 bucket used for Terraform state"
  value       = aws_s3_bucket.state.id
}

output "bucket_arn" {
  description = "ARN of the S3 bucket used for Terraform state"
  value       = aws_s3_bucket.state.arn
}
