# Applied locally (Google Application Default Credentials: `gcloud auth
# application-default login`); deliberately absent from
# .github/workflows/terraform.yaml's plan/apply matrix for the same reason
# every root at the bottom of a trust chain must be — this root provisions
# the very identities the OTHER workflows authenticate with, so it can never
# itself depend on being reachable from CI. Everything here is permanent
# (prevent_destroy) and changes rarely.
module "gcp_wif" {
  source = "../modules/gcp_wif"
}

module "gcp_github_oidc" {
  source = "../modules/gcp_github_oidc"

  github_repo_owner    = "viacheslavbinetskyiaws-ctrl"
  github_repo_owner_id = "327975409"
  github_repo          = "events-api"
  bigquery_project     = "project-e8569bd6-524d-42fe-bb9"
  bigquery_dataset     = "events_analytics"
}
