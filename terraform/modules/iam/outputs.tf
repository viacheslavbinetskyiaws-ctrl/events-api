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

output "k8s_viewer_role_arn" {
  description = "IAM role for the least-privilege K8s RBAC demo — no AWS permissions attached, only trusted to authenticate to EKS via an access-entry Kubernetes group"
  value       = aws_iam_role.k8s_viewer.arn
}

output "alb_controller_role_arn" {
  description = "IRSA role ARN for the AWS Load Balancer Controller's kube-system ServiceAccount"
  value       = aws_iam_role.alb_controller_irsa.arn
}
