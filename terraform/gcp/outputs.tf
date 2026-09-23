output "github_actions_workload_identity_provider" {
  description = "Set as the GCP_WORKLOAD_IDENTITY_PROVIDER repository variable"
  value       = module.gcp_github_oidc.workload_identity_provider
}

output "github_actions_service_account" {
  description = "Set as the GCP_SERVICE_ACCOUNT repository variable"
  value       = module.gcp_github_oidc.service_account_email
}
