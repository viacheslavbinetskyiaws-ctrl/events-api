output "endpoint" {
  description = "Postgres connection endpoint (address:port)"
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Postgres hostname, without port"
  value       = aws_db_instance.this.address
}

output "resource_id" {
  description = "RDS DbiResourceId, not the identifier — needed by the rds-db:connect IAM policy ARN in the next step"
  value       = aws_db_instance.this.resource_id
}

output "master_user_secret_arn" {
  description = "Secrets Manager ARN holding the master password, for the one-time GRANT rds_iam bootstrap step"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
