output "state_bucket_id" {
  description = "Name of the real S3 bucket holding Terraform state"
  value       = module.s3.bucket_id
}

output "ecr_repository_urls" {
  description = "ECR repository URLs for all four project images"
  value       = module.ecr.repository_urls
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

output "github_terraform_plan_role_arn" {
  description = "OIDC-federated, read-only role GitHub Actions assumes to run terraform plan on PRs"
  value       = module.github_oidc.terraform_plan_role_arn
}

output "github_bootstrap_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes for cluster-up/cluster-down Kubernetes work"
  value       = module.github_oidc.bootstrap_role_arn
}

output "debezium_secret_arn" {
  description = "Secrets Manager container for the debezium_replication password"
  value       = aws_secretsmanager_secret.debezium.arn
}
