data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Must equal "${name_prefix}-eks" as derived inside modules/eks (see
  # terraform/cluster/main.tf, which uses the same literal prefix). Computed
  # here instead of read from module.eks so this root never depends on an
  # ephemeral resource: CI must be able to destroy the cluster without
  # touching, or being unable to plan, the identities it runs on.
  cluster_name    = "events-api-eks"
  eks_cluster_arn = "arn:aws:eks:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:cluster/${local.cluster_name}"

  # Both ECR (repositories) and the Debezium secret container now live in
  # terraform/cluster (2026-09-22 cost decision: destroy them on `down`
  # rather than pay to keep them forever, matching everything else this
  # project already treats as disposable). Computed here, not read via a
  # live `data` lookup, for the same reason as eks_cluster_arn above: this
  # root's own plan/apply must never depend on the ephemeral stack existing.
  ecr_repo_names = ["app", "streaming", "dbt", "realtime", "kafka-connect"]
  ecr_repository_arns = [
    for name in local.ecr_repo_names :
    "arn:aws:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/events-api-${name}"
  ]

  # Secrets Manager appends a random 6-character suffix to every secret's own
  # ARN, which can't be predicted the way an ECR or EKS ARN can — AWS's own
  # documented fix is the `??????` wildcard, which "enables you to securely
  # grant permissions to a secret that doesn't yet exist" and automatically
  # covers a delete/recreate cycle under the same name (confirmed against
  # AWS's IAM identity-based-policy examples doc, not assumed).
  debezium_secret_arn_pattern = "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:events-api/debezium-replication-??????"
}

module "s3" {
  source      = "./modules/s3"
  name_prefix = "events-api"
}

module "github_oidc" {
  source = "./modules/github-oidc"

  name_prefix         = "events-api-github"
  github_owner        = "viacheslavbinetskyiaws-ctrl"
  github_owner_id     = "327975409"
  github_repo         = "events-api"
  github_repo_id      = "1366376677"
  eks_cluster_arn     = local.eks_cluster_arn
  debezium_secret_arn = local.debezium_secret_arn_pattern
  state_bucket_arn    = "arn:aws:s3:::${module.s3.bucket_id}"
  ecr_repository_arns = local.ecr_repository_arns
}
