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

# Separate trust policy for the read-only plan role — pull_request's sub
# claim is branch-agnostic (repo:OWNER@ID/REPO@ID:pull_request, no ref),
# so this must never be shared with a role that holds write access.
data "aws_iam_policy_document" "github_trust_pull_request" {
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
      values   = ["repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:pull_request"]
    }
  }
}

# --- terraform_plan: runs `terraform plan` on every PR touching terraform/ ---
# ReadOnlyAccess only — plan never needs write access, and pull_request's
# branch-agnostic sub claim means this is the more exposed trigger of the two.

resource "aws_iam_role" "terraform_plan" {
  name               = "${var.name_prefix}-terraform-plan"
  assume_role_policy = data.aws_iam_policy_document.github_trust_pull_request.json
}

resource "aws_iam_role_policy_attachment" "terraform_plan" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# terraform plan still needs to acquire/release the S3-native state lock
# (backend.tf's use_lockfile = true) even though it makes no other writes —
# ReadOnlyAccess alone can't create the .tflock object. Key path matches
# backend.tf's literal `key = "events-api/terraform.tfstate"` exactly; if
# that ever changes, this must change with it (backend blocks can't
# reference variables, so this coupling can't be made a shared value).
data "aws_iam_policy_document" "terraform_plan_state_lock" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.state_bucket_arn}/events-api/terraform.tfstate.tflock"]
  }
}

resource "aws_iam_policy" "terraform_plan_state_lock" {
  name   = "${var.name_prefix}-terraform-plan-state-lock"
  policy = data.aws_iam_policy_document.terraform_plan_state_lock.json
}

resource "aws_iam_role_policy_attachment" "terraform_plan_state_lock" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = aws_iam_policy.terraform_plan_state_lock.arn
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
