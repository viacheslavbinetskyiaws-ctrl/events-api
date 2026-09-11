data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.name_prefix}-app-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

data "aws_iam_policy_document" "logs" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_policy" "logs" {
  name   = "${var.name_prefix}-logs-policy"
  policy = data.aws_iam_policy_document.logs.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.logs.arn
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app_irsa" {
  name               = "${var.name_prefix}-app-irsa"
  assume_role_policy = data.aws_iam_policy_document.irsa_trust.json
}

data "aws_iam_policy_document" "rds_connect" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:aws:rds-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:dbuser:${var.rds_resource_id}/${var.rds_db_user}"]
  }
}

resource "aws_iam_policy" "rds_connect" {
  name   = "${var.name_prefix}-rds-connect"
  policy = data.aws_iam_policy_document.rds_connect.json
}

resource "aws_iam_role_policy_attachment" "app_irsa_rds" {
  role       = aws_iam_role.app_irsa.name
  policy_arn = aws_iam_policy.rds_connect.arn
}

data "aws_iam_policy_document" "kafka_connect_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-connect-connect"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kafka_connect_gcp_irsa" {
  name               = "${var.name_prefix}-kafka-connect-gcp-irsa"
  assume_role_policy = data.aws_iam_policy_document.kafka_connect_irsa_trust.json
}


data "aws_iam_policy_document" "migration_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-api-migrate"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "migration_irsa" {
  name               = "${var.name_prefix}-migration-irsa"
  assume_role_policy = data.aws_iam_policy_document.migration_irsa_trust.json
}

data "aws_iam_policy_document" "migration_rds_connect" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:aws:rds-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:dbuser:${var.rds_resource_id}/events"]
  }
}

resource "aws_iam_policy" "migration_rds_connect" {
  name   = "${var.name_prefix}-migration-rds-connect"
  policy = data.aws_iam_policy_document.migration_rds_connect.json
}

resource "aws_iam_role_policy_attachment" "migration_irsa_rds" {
  role       = aws_iam_role.migration_irsa.name
  policy_arn = aws_iam_policy.migration_rds_connect.arn
}

data "aws_iam_policy_document" "dbt_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-api-dbt"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dbt_irsa" {
  name               = "${var.name_prefix}-dbt-irsa"
  assume_role_policy = data.aws_iam_policy_document.dbt_irsa_trust.json
}

data "aws_iam_policy_document" "dbt_rds_connect" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:aws:rds-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:dbuser:${var.rds_resource_id}/events"]
  }
}


resource "aws_iam_policy" "dbt_rds_connect" {
  name   = "${var.name_prefix}-dbt-rds-connect"
  policy = data.aws_iam_policy_document.dbt_rds_connect.json
}

resource "aws_iam_role_policy_attachment" "dbt_irsa_rds" {
  role       = aws_iam_role.dbt_irsa.name
  policy_arn = aws_iam_policy.dbt_rds_connect.arn
}

# cloudwatch:PutMetricData has no resource-level scoping in IAM — Resource
# "*" is a documented AWS constraint on this specific action, not a design
# gap.
data "aws_iam_policy_document" "dbt_cloudwatch_put_metric" {
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "dbt_cloudwatch_put_metric" {
  name   = "${var.name_prefix}-dbt-cloudwatch-put-metric"
  policy = data.aws_iam_policy_document.dbt_cloudwatch_put_metric.json
}

resource "aws_iam_role_policy_attachment" "dbt_irsa_cloudwatch" {
  role       = aws_iam_role.dbt_irsa.name
  policy_arn = aws_iam_policy.dbt_cloudwatch_put_metric.arn
}

# Zero AWS permissions attached, deliberately — same shape as
# kafka_connect_gcp_irsa above: this role's only job is proving "this is a
# legitimate AWS-authenticated caller." All real authorization for this
# identity comes from Kubernetes RBAC (see k8s/overlays/aws/viewer-rbac.yaml),
# not from anything IAM grants it.
#
# Trust principal hardcoded to the known human-operator IAM user, not
# data.aws_caller_identity.current.arn — CI (GitHub Actions' OIDC-federated
# terraform_apply) has no legitimate reason to ever assume this
# demo/testing role itself, and deriving it dynamically just meant this
# trust policy churned every time the identity running Terraform switched
# between the human operator and CI. Same fix as modules/eks/main.tf's
# aws_eks_access_entry.creator.
data "aws_iam_policy_document" "k8s_viewer_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/terraform-events-api"]
    }
  }
}

resource "aws_iam_role" "k8s_viewer" {
  name               = "${var.name_prefix}-k8s-viewer"
  assume_role_policy = data.aws_iam_policy_document.k8s_viewer_trust.json
}

# The ALB Controller's IRSA role — real AWS permissions attached (unlike
# k8s_viewer/kafka_connect_gcp_irsa above), since this identity actually
# calls the EC2/ELB APIs to create and manage real load balancers.
data "aws_iam_policy_document" "alb_controller_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "alb_controller_irsa" {
  name               = "${var.name_prefix}-alb-controller-irsa"
  assume_role_policy = data.aws_iam_policy_document.alb_controller_irsa_trust.json
}

resource "aws_iam_policy" "alb_contoroller" {
  name   = "${var.name_prefix}-alb-controller-policy"
  policy = file("${path.module}/alb-controller-policy.json")
}

resource "aws_iam_role_policy_attachment" "alb_controller_irsa" {
  role       = aws_iam_role.alb_controller_irsa.name
  policy_arn = aws_iam_policy.alb_contoroller.arn
}
