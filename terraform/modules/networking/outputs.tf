
output "vpc_id" {
  description = "ID of VPC"
  value       = aws_vpc.main.id
}

output "subnet_ids" {
  description = "List of Subnet IDs"
  value       = aws_subnet.public[*].id
}

output "aws_security_group" {
  description = "Security group ID"
  value       = aws_security_group.app.id
}

output "private_subnet_ids" {
  description = "List of private Subnet IDs"
  value       = aws_subnet.private[*].id
}
