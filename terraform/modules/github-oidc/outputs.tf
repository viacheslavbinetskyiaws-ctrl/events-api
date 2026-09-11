output "ecr_push_role_arn" {
  value = aws_iam_role.ecr_push.arn
}

output "terraform_apply_role_arn" {
  value = aws_iam_role.terraform_apply.arn
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

output "terraform_plan_role_arn" {
  value = aws_iam_role.terraform_plan.arn
}
