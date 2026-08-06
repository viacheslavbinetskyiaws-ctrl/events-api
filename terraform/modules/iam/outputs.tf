output "role_arn" {
  type  = string
  value = aws_iam_role.app.arn
}

output "role_name" {
  type  = string
  value = aws_iam_role.app.name
}
