output "workload_identity_provider" {
  description = "Full resource name for google-github-actions/auth's workload_identity_provider input"
  value       = "projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_actions.workload_identity_pool_id}/providers/${google_iam_workload_identity_pool_provider.github_actions.workload_identity_pool_provider_id}"
}

output "service_account_email" {
  description = "Service account GitHub Actions impersonates for google-github-actions/auth's service_account input"
  value       = google_service_account.github_actions_verify.email
}
