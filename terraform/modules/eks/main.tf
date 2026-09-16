resource "aws_iam_role" "cluster" {
  name = "${var.name_prefix}-eks-cluster"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  name     = "${var.name_prefix}-eks"
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids = var.cluster_subnet_ids
  }

  access_config {
    authentication_mode = "API"
  }

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

resource "aws_iam_openid_connect_provider" "cluster" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "node" {
  name = "${var.name_prefix}-eks-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# resource "aws_launch_template" "node" {
#   name_prefix = "${var.name_prefix}-node-"

#   tag_specifications {
#     resource_type = "instance"
#     tags = {
#       Name = "${var.name_prefix}-node"
#     }
#   }
# }

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.node_subnet_ids
  instance_types  = ["t4g.small"]
  ami_type        = "AL2023_ARM_64_STANDARD"

  scaling_config {
    desired_size = 3
    min_size     = 1
    max_size     = 3
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]

  # launch_template {
  #   id      = aws_launch_template.node.id
  #   version = aws_launch_template.node.latest_version
  # }
}

resource "aws_iam_role" "node_kafka_connect" {
  name = "${var.name_prefix}-eks-node-kafka-connect"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node_kafka_connect_worker" {
  role       = aws_iam_role.node_kafka_connect.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_kafka_connect_cni" {
  role       = aws_iam_role.node_kafka_connect.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_kafka_connect_ecr" {
  role       = aws_iam_role.node_kafka_connect.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_launch_template" "kafka_connect_node" {
  name_prefix = "${var.name_prefix}-kafka-connect-node-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.name_prefix}-kafka-connect-node"
    }
  }
}

resource "aws_eks_node_group" "kafka_connect" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-kafka-connect"
  node_role_arn   = aws_iam_role.node_kafka_connect.arn
  subnet_ids      = var.node_subnet_ids
  instance_types  = ["t4g.small"]
  ami_type        = "AL2023_ARM_64_STANDARD"

  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = 1
  }

  launch_template {
    id      = aws_launch_template.kafka_connect_node.id
    version = aws_launch_template.kafka_connect_node.latest_version
  }

  taint {
    key    = "dedicated"
    value  = "kafka-connect"
    effect = "NO_SCHEDULE"
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_kafka_connect_worker,
    aws_iam_role_policy_attachment.node_kafka_connect_cni,
    aws_iam_role_policy_attachment.node_kafka_connect_ecr,
  ]
}

data "aws_caller_identity" "current" {}

# Hardcoded to the known human-operator IAM user, not derived from
# data.aws_caller_identity.current.arn — that resolves to whoever is
# *currently* authenticated, which broke the moment Terraform started
# also running via an assumed role (GitHub Actions' OIDC-federated
# terraform_apply): an assumed-role session ARN
# (arn:aws:sts::...:assumed-role/.../SessionName) is a different format
# EKS's access-entry API rejects outright, unlike a plain IAM ARN. Matches
# the sibling `root` access entry below, which was never dynamically
# derived in the first place.
resource "aws_eks_access_entry" "creator" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/terraform-events-api"
}

resource "aws_eks_access_policy_association" "creator_admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/terraform-events-api"
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

resource "aws_eks_access_entry" "root" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
}

resource "aws_eks_access_policy_association" "root_admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

# Deliberately no aws_eks_access_policy_association here — this identity's
# only path to any permission is the hand-written Role/RoleBinding in
# k8s/overlays/aws/viewer-rbac.yaml, not an AWS-managed policy fallback.
resource "aws_eks_access_entry" "k8s_viewer" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.k8s_viewer_role_arn

  kubernetes_groups = ["events-api-viewers"]
}

# Same shape as k8s_viewer above — no AWS-managed policy fallback,
# permissions come entirely from k8s/overlays/aws/deploy-rbac.yaml.
resource "aws_eks_access_entry" "github_deploy" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.github_deploy_role_arn

  kubernetes_groups = ["github-actions-deployers"]
}

data "aws_iam_policy_document" "ebs_csi_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")}:sub"
      values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name_prefix}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_irsa_trust.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs_csi.arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "vpc-cni"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  configuration_values = jsonencode({
    resources = {
      requests = {
        cpu    = "25m"
        memory = "64Mi"
      }
      limits = {
        cpu    = "50m"
        memory = "128Mi"
      }
    }
    nodeAgent = {
      resources = {
        requests = {
          cpu    = "10m"
          memory = "32Mi"
        }
        limits = {
          cpu    = "25m"
          memory = "64Mi"
        }
      }
    }
  })
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "kube-proxy"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  configuration_values = jsonencode({
    resources = {
      requests = {
        cpu    = "100m"
        memory = "48Mi"
      }
      limits = {
        cpu    = "200m"
        memory = "96Mi"
      }
    }
  })
}
