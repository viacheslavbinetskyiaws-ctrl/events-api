terraform {
  backend "s3" {
    bucket       = "events-api-tfstate-938500344309"
    key          = "events-api/cluster.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
