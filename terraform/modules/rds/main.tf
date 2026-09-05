resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "db" {
  name_prefix = "${var.name_prefix}-db-sg-"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-db-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_postgres" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = var.cluster_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_parameter_group" "logical_replication" {
  name   = "${var.name_prefix}-postgres18-logical-replication"
  family = "postgres18"

  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  db_name           = "events"
  username          = "events"

  manage_master_user_password = true
  storage_encrypted           = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  iam_database_authentication_enabled = true
  publicly_accessible                 = false
  skip_final_snapshot                 = true

  parameter_group_name = aws_db_parameter_group.logical_replication.name

  tags = {
    Name = "${var.name_prefix}-db"
  }
}

# resource "aws_vpc_security_group_egress_rule" "db_all" {
#   security_group_id = aws_security_group.db.id
#   cidr_ipv4         = "0.0.0.0/0"
#   ip_protocol       = "-1"
# }
