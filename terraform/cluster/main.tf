module "networking" {
  source = "../modules/networking"

  vpc_cidr    = "10.0.0.0/16"
  name_prefix = "events-api"
}

module "iam" {
  source = "../modules/iam"

  name_prefix           = "events-api-iam"
  oidc_provider_arn     = module.eks.oidc_provider_arn
  oidc_provider_url     = module.eks.oidc_provider_url
  k8s_namespace         = "events-api"
  k8s_service_account   = "events-api-app"
  rds_resource_id       = module.rds.resource_id
  rds_db_user           = "events_app"
  rds_master_secret_arn = module.rds.master_user_secret_arn
  debezium_secret_arn   = data.aws_secretsmanager_secret.debezium.arn
}

module "eks" {
  source = "../modules/eks"

  # The cluster is named "${name_prefix}-eks" inside modules/eks. The
  # foundation root (terraform/main.tf) computes its cluster ARN from the
  # literal "events-api-eks", so this prefix must not change without
  # changing that local too.
  name_prefix            = "events-api"
  cluster_subnet_ids     = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids        = module.networking.private_subnet_ids
  k8s_viewer_role_arn    = module.iam.k8s_viewer_role_arn
  github_deploy_role_arn = data.aws_iam_role.github_deploy.arn
  bootstrap_role_arn     = data.aws_iam_role.github_bootstrap.arn
}

module "rds" {
  source = "../modules/rds"

  name_prefix               = "events-api"
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  cluster_security_group_id = module.eks.cluster_security_group_id
}
