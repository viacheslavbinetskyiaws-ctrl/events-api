variable "github_repo_owner" {
  description = "GitHub org/user that owns the repo, e.g. viacheslavbinetskyiaws-ctrl"
  type        = string
}

variable "github_repo_owner_id" {
  description = "GitHub's numeric ID for github_repo_owner (same value the AWS github-oidc module trusts)"
  type        = string
}

variable "github_repo" {
  description = "Bare repo name, e.g. events-api"
  type        = string
}

variable "bigquery_project" {
  description = "GCP project ID holding the BigQuery dataset CI needs to read"
  type        = string
}

variable "bigquery_dataset" {
  description = "BigQuery dataset CI's verification script queries (events_analytics)"
  type        = string
}
