variable "name_prefix" {
  type = string
}

variable "github_owner" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "eks_cluster_arn" {
  type = string
}

variable "state_bucket_arn" {
  type = string
}

variable "github_owner_id" {
  type = string
}
variable "github_repo_id" {
  type = string
}
