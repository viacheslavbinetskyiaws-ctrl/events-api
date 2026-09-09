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
