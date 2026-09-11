resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # No thumbprint_list — AWS verifies the JWKS endpoint's TLS cert
  # directly against its trusted CA store (since July 2023), same
  # reasoning already applied to EKS's own OIDC provider in
  # modules/eks/main.tf.
}

# Shared by all three roles below — every one of them is only ever
# assumable by a workflow run on this repo's main branch.
data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:ref:refs/heads/main"]
    }
  }
}

# --- ecr_push: builds+pushes the 4 app images on merge to main ---

resource "aws_iam_role" "ecr_push" {
  name               = "${var.name_prefix}-ecr-push"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "ecr_push" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
    ]
    resources = var.ecr_repository_arns
  }
}

resource "aws_iam_policy" "ecr_push" {
  name   = "${var.name_prefix}-ecr-push-policy"
  policy = data.aws_iam_policy_document.ecr_push.json
}

resource "aws_iam_role_policy_attachment" "ecr_push" {
  role       = aws_iam_role.ecr_push.name
  policy_arn = aws_iam_policy.ecr_push.arn
}

# --- terraform_apply: runs `terraform apply` from CI, approval-gated ---
# AdministratorAccess, matching this project's own already-accepted
# precedent (terraform-events-api/root both hold cluster-admin-equivalent
# power on this single-operator account). A least-privilege Terraform
# policy covering this repo's full resource footprint is a separate,
# substantial exercise outside this milestone's scope.

resource "aws_iam_role" "terraform_apply" {
  name               = "${var.name_prefix}-terraform-apply"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

resource "aws_iam_role_policy_attachment" "terraform_apply" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# --- deploy: runs `kubectl set image` against the live cluster ---
# Deliberately zero broad AWS permissions — only enough to resolve the
# cluster's connection details for `aws eks update-kubeconfig`. Real
# authority comes entirely from the EKS access entry + hand-written
# Role/RoleBinding (modules/eks/ + k8s/overlays/aws/deploy-rbac.yaml),
# mirroring k8s_viewer's existing shape exactly.

resource "aws_iam_role" "deploy" {
  name               = "${var.name_prefix}-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "deploy_describe_cluster" {
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [var.eks_cluster_arn]
  }
}

resource "aws_iam_policy" "deploy_describe_cluster" {
  name   = "${var.name_prefix}-deploy-describe-cluster"
  policy = data.aws_iam_policy_document.deploy_describe_cluster.json
}

resource "aws_iam_role_policy_attachment" "deploy_describe_cluster" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_describe_cluster.arn
}
