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

  parameter {
    # 5000 (~4.88GiB) was sized against the original 50GiB allocated_storage
    # (~10% margin) and never revisited when Milestone 11 shrank storage to
    # the verified 5GiB floor — at that scale it was ~98% of total storage
    # per slot, capable of starving the instance on its own. Lowered to
    # 1000 (~0.98GiB): real WAL generation here is tiny (a normal Kafka
    # Connect reconnect generates a few MB, confirmed live), so this
    # comfortably survives realistic transient events while capping
    # worst-case exposure at ~20% of total storage per slot instead of ~98%.
    name         = "max_slot_wal_keep_size"
    value        = "1000"
    apply_method = "immediate"
  }

  # Both are postgres18's stock family defaults, never explicitly set by
  # this project — same "never revisited after shrinking storage" gap as
  # max_slot_wal_keep_size, just for parameters that were never customized
  # at all rather than customized against the old 50GiB assumption.
  parameter {
    # wal_keep_size reserves WAL for physical streaming replicas — this
    # deployment has none (no read replicas, multi_az = false, confirmed
    # live). Serves no purpose here; 0 is its documented minimum.
    name         = "wal_keep_size"
    value        = "0"
    apply_method = "immediate"
  }

  parameter {
    # Real average checkpoint-cycle WAL generation is ~26.5MB (confirmed
    # live via pg_stat_checkpointer's buffers_written across 325
    # checkpoints) — 2048MB is wildly oversized for this workload. 256MB
    # keeps a 10x+ margin over any realistic burst without meaningfully
    # increasing checkpoint frequency.
    name         = "max_wal_size"
    value        = "256"
    apply_method = "immediate"
  }

  parameter {
    # min_wal_size must stay <= max_wal_size; lowered to the allowed floor
    # for consistency, trivial marginal benefit on its own.
    name         = "min_wal_size"
    value        = "128"
    apply_method = "immediate"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  instance_class = "db.t4g.micro"

  allocated_storage     = 5
  max_allocated_storage = 0
  db_name               = "events"
  username              = "events"

  manage_master_user_password = true
  storage_encrypted           = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  iam_database_authentication_enabled = true
  publicly_accessible                 = false
  skip_final_snapshot                 = true
  apply_immediately                   = true

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
