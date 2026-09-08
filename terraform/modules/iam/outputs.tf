output "role_arn" {
  type  = string
  value = aws_iam_role.app.arn
}

output "role_name" {
  type  = string
  value = aws_iam_role.app.name
}

output "app_irsa_role_arn" {
  value = aws_iam_role.app_irsa.arn
}

output "kafka_connect_gcp_irsa_role_arn" {
  description = "IRSA role ARN for Kafka Connect's GCP Workload Identity Federation handshake"
  value       = aws_iam_role.kafka_connect_gcp_irsa.arn
}

output "migration_irsa_role_arn" {
  description = "IRSA role ARN for the migration Job's IAM auth as the owner role"
  value       = aws_iam_role.migration_irsa.arn
}

output "dbt_irsa_role_arn" {
  description = "IRSA role ARN for the dbt CronJob's IAM auth as the owner role, plus CloudWatch metric push"
  value       = aws_iam_role.dbt_irsa.arn
}
