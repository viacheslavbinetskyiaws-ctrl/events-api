output "role_arn" {
  type  = string
  value = aws_iam_role.app.arn
}

output "role_name" {
  type  = string
  value = aws_iam_role.app.name
}

output "app_irsa_role_arn" {
  value = aws_iam_role.app_irsa.arn
}
