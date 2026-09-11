output "state_bucket_id" {
  description = "Name of the real S3 bucket holding Terraform state"
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

output "dbt_irsa_role_arn" {
  description = "IRSA role ARN for the dbt CronJob's IAM auth as the owner role, plus CloudWatch metric push"
  value       = module.iam.dbt_irsa_role_arn
}

output "k8s_viewer_role_arn" {
  description = "IAM role for the least-privilege K8s RBAC demo — no AWS permissions attached, only trusted to authenticate to EKS via an access-entry Kubernetes group"
  value       = module.iam.k8s_viewer_role_arn
}

output "alb_controller_role_arn" {
  description = "IRSA role ARN for the AWS Load Balancer Controller's kube-system ServiceAccount"
  value       = module.iam.alb_controller_role_arn
}

output "github_ecr_push_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to push images to ECR"
  value       = module.github_oidc.ecr_push_role_arn
}

output "github_terraform_apply_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to run terraform apply"
  value       = module.github_oidc.terraform_apply_role_arn
}

output "github_deploy_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to deploy to EKS"
  value       = module.github_oidc.deploy_role_arn
}
