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

module "networking" {
  source = "./modules/networking"

  vpc_cidr    = "10.0.0.0/16"
  name_prefix = "events-api"
}

module "iam" {
  source = "./modules/iam"

  name_prefix         = "events-api-iam"
  oidc_provider_arn   = module.eks.oidc_provider_arn
  oidc_provider_url   = module.eks.oidc_provider_url
  k8s_namespace       = "events-api"
  k8s_service_account = "events-api-app"
  rds_resource_id     = module.rds.resource_id
  rds_db_user         = "events_app"
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

module "eks" {
  source = "./modules/eks"

  name_prefix            = "events-api"
  cluster_subnet_ids     = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids        = module.networking.private_subnet_ids
  k8s_viewer_role_arn    = module.iam.k8s_viewer_role_arn
  github_deploy_role_arn = module.github_oidc.deploy_role_arn
}

module "rds" {
  source = "./modules/rds"

  name_prefix               = "events-api"
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  cluster_security_group_id = module.eks.cluster_security_group_id
}
