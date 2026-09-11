
output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded cluster CA certificate, for kubeconfig"
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "oidc_provider_arn" {
  description = "IRSA OIDC provider ARN, needed by Milestone 2's IAM role trust policies"
  value       = aws_iam_openid_connect_provider.cluster.arn
}

output "oidc_provider_url" {
  description = "IRSA OIDC issuer URL, needed by Milestone 2's IAM role trust policies"
  value       = replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")
}

output "cluster_security_group_id" {
  description = "EKS-managed cluster security group — nodes/pods use this for network communication, the correct RDS ingress source"
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "kafka_connect_node_role_arn" {
  description = "Node IAM role for the dedicated Kafka Connect node group — this is the AWS identity GCP WIF must trust now, not the old IRSA role"
  value       = aws_iam_role.node_kafka_connect.arn
}

output "cluster_arn" {
  value = aws_eks_cluster.this.arn
}
