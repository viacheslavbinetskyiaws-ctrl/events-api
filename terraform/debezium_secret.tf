
# Container only. The value is generated and stored at runtime by the
# cluster-up bootstrap Job (Plan 2) and never passes through Terraform, so it
# never lands in state. Persistent by design: keeping the same password across
# teardown cycles means each `up` only has to ALTER ROLE to match it.
resource "aws_secretsmanager_secret" "debezium" {
  name        = "events-api/debezium-replication"
  description = "Password for the debezium_replication Postgres role. Value written by the cluster-up bootstrap Job, not by Terraform."

  lifecycle {
    prevent_destroy = true
  }
}
