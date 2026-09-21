output "cluster_name" {
  description = "EKS cluster name, for aws eks update-kubeconfig"
  value       = module.eks.cluster_name
}

output "region" {
  value = data.aws_region.current.region
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "rds_address" {
  description = "Postgres hostname without port: what manifests and Jobs use as the DB host"
  value       = module.rds.address
}

output "rds_endpoint" {
  description = "Postgres connection endpoint (address:port)"
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "Secrets Manager ARN holding the RDS master password (read only by bootstrap Job 1)"
  value       = module.rds.master_user_secret_arn
}

output "eks_oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "eks_oidc_provider_url" {
  value = module.eks.oidc_provider_url
}

output "kafka_connect_node_role_arn" {
  value = module.eks.kafka_connect_node_role_arn
}

output "app_irsa_role_arn" {
  value = module.iam.app_irsa_role_arn
}

output "kafka_connect_gcp_irsa_role_arn" {
  value = module.iam.kafka_connect_gcp_irsa_role_arn
}

output "migration_irsa_role_arn" {
  value = module.iam.migration_irsa_role_arn
}

output "dbt_irsa_role_arn" {
  value = module.iam.dbt_irsa_role_arn
}

output "k8s_viewer_role_arn" {
  value = module.iam.k8s_viewer_role_arn
}

output "alb_controller_role_arn" {
  value = module.iam.alb_controller_role_arn
}

output "bootstrap_master_irsa_role_arn" {
  value = module.iam.bootstrap_master_irsa_role_arn
}

output "bootstrap_roles_irsa_role_arn" {
  value = module.iam.bootstrap_roles_irsa_role_arn
}
