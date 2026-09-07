output "state_bucket_id" {
  description = "Name of the real S3 bucket holding Terraform state, once created"
  value       = module.s3.bucket_id
}

output "ecr_repository_urls" {
  description = "ECR repository URLs for all four project images"
  value       = module.ecr.repository_urls
}

output "eks_cluster_name" {
  description = "EKS cluster name, for aws eks update-kubeconfig"
  value       = module.eks.cluster_name
}

output "eks_oidc_provider_arn" {
  description = "IRSA OIDC provider ARN, needed by Milestone 2's IAM role trust policies"
  value       = module.eks.oidc_provider_arn
}

output "eks_oidc_provider_url" {
  description = "IRSA OIDC issuer URL, needed by Milestone 2's IAM role trust policies"
  value       = module.eks.oidc_provider_url
}

output "rds_endpoint" {
  description = "Postgres connection endpoint (address:port)"
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "Secrets Manager ARN holding the RDS master password, for the one-time GRANT rds_iam bootstrap"
  value       = module.rds.master_user_secret_arn
}

output "app_irsa_role_arn" {
  description = "IRSA role ARN for the app's ServiceAccount annotation in k8s/overlays/aws/"
  value       = module.iam.app_irsa_role_arn
}

output "kafka_connect_gcp_irsa_role_arn" {
  description = "IRSA role ARN for Kafka Connect's GCP WIF handshake"
  value       = module.iam.kafka_connect_gcp_irsa_role_arn
}

output "kafka_connect_node_role_arn" {
  description = "Node IAM role for the dedicated Kafka Connect node group — rebind GCP WIF's AWS provider trust to this ARN"
  value       = module.eks.kafka_connect_node_role_arn
}

output "migration_irsa_role_arn" {
  description = "IRSA role ARN for the migration Job's IAM auth as the owner role"
  value       = module.iam.migration_irsa_role_arn
}
