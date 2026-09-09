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

module "eks" {
  source = "./modules/eks"

  name_prefix         = "events-api"
  cluster_subnet_ids  = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids     = module.networking.private_subnet_ids
  k8s_viewer_role_arn = module.iam.k8s_viewer_role_arn
}

module "rds" {
  source = "./modules/rds"

  name_prefix               = "events-api"
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  cluster_security_group_id = module.eks.cluster_security_group_id
}
