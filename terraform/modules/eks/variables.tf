variable "name_prefix" {
  type = string
}

variable "cluster_subnet_ids" {
  type = list(string)
}

variable "node_subnet_ids" {
  type = list(string)
}

variable "k8s_viewer_role_arn" {
  type        = string
  description = "IAM role ARN mapped into the cluster via kubernetes_groups, authorized only through hand-written K8s RBAC — no access policy attached"
}

variable "github_deploy_role_arn" {
  type = string
}

variable "bootstrap_role_arn" {
  type        = string
  description = "GitHub OIDC bootstrap role: granted EKS cluster-admin so cluster-up/down can install Helm charts and apply manifests"
}
