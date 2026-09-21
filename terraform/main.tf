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
}

module "s3" {
  source      = "./modules/s3"
  name_prefix = "events-api"
}

module "ecr" {
  source      = "./modules/ecr"
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
  debezium_secret_arn = aws_secretsmanager_secret.debezium.arn
  state_bucket_arn    = "arn:aws:s3:::${module.s3.bucket_id}"

  ecr_repository_arns = [
    module.ecr.repository_arns["app"],
    module.ecr.repository_arns["streaming"],
    module.ecr.repository_arns["dbt"],
    module.ecr.repository_arns["realtime"],
    module.ecr.repository_arns["kafka-connect"],
  ]
}
