data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Persistent identities and resources owned by the foundation root
# (terraform/). Looked up by their fixed names so this root depends on them
# one-way and can be destroyed without touching them.
data "aws_iam_role" "github_deploy" {
  name = "events-api-github-deploy"
}

data "aws_iam_role" "github_bootstrap" {
  name = "events-api-github-bootstrap"
}

data "aws_sns_topic" "dbt_build_alerts" {
  name = "events-api-dbt-build-alerts"
}
