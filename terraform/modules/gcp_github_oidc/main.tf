# Lets GitHub Actions (this repo, main branch only) authenticate to GCP with
# no static key anywhere — same "OIDC everywhere, zero long-lived cloud
# credentials in CI" posture the AWS side already has via
# terraform/modules/github-oidc/. Separate pool from modules/gcp_wif's
# kafka_connect one on purpose: that pool federates from an AWS IAM role
# (Kafka Connect's own BigQuery-sink authentication, running inside the
# cluster); this one federates from GitHub's own OIDC issuer directly, a
# different identity source entirely.

data "google_project" "current" {
  project_id = var.bigquery_project
}

resource "google_iam_workload_identity_pool" "github_actions" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions OIDC federation"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_actions.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"

  # Mirrors the AWS github-oidc module's own trust restriction exactly: only
  # a workflow run on this repo's main branch, enforced by the provider
  # itself (not by convention) — repository_owner_id is GitHub's stable
  # numeric ID (same value passed to modules/github-oidc), not the
  # rename-able owner string, for the same reason the AWS module keys off it.
  attribute_condition = <<-EOT
    assertion.repository_owner_id == "${var.github_repo_owner_id}" &&
    attribute.repository == "${var.github_repo_owner}/${var.github_repo}" &&
    assertion.ref == "refs/heads/main" &&
    assertion.ref_type == "branch"
  EOT

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Narrowly scoped to this one purpose — deliberately NOT the existing
# big-query service account modules/gcp_wif's kafka_connect pool impersonates
# (that one writes to BigQuery for the sink connector; CI only ever reads).
resource "google_service_account" "github_actions_verify" {
  account_id   = "github-actions-verify"
  display_name = "GitHub Actions - read-only cluster-up verification"
}

resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = google_service_account.github_actions_verify.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_actions.workload_identity_pool_id}/attribute.repository/${var.github_repo_owner}/${var.github_repo}"
}

# dataViewer scoped to the one dataset the verification script actually
# queries — BigQuery has no dataset-scoped equivalent for jobUser (running a
# query job is inherently project-level; the actual data read is what
# dataViewer gates, scoped tightly below).
resource "google_bigquery_dataset_iam_member" "verify_data_viewer" {
  project    = var.bigquery_project
  dataset_id = var.bigquery_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.github_actions_verify.email}"
}

resource "google_project_iam_member" "verify_job_user" {
  project = var.bigquery_project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.github_actions_verify.email}"
}
