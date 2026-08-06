module "networking" {
  source = "./modules/networking"

  vpc_cidr    = "10.0.0.0/16"
  name_prefix = "events-api"
}

module "iam" {
  source = "./modules/iam"

  name_prefix = "events-api-iam"
}

module "s3" {
  source      = "./modules/s3"
  name_prefix = "events-api"
}
