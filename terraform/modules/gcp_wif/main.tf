resource "google_iam_workload_identity_pool" "kafka_connect" {
  workload_identity_pool_id = "kafka-connect-pool"
  display_name              = "Kafka Connect AWS federation"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_iam_workload_identity_pool_provider" "eks_kafka_connect" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.kafka_connect.workload_identity_pool_id
  workload_identity_pool_provider_id = "eks-kafka-connect"

  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role_name}/')"
  }
  attribute_condition = "assertion.arn.startsWith('arn:aws:sts::938500344309:assumed-role/events-api-eks-node-kafka-connect/')"

  aws {
    account_id = "938500344309"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = "projects/project-e8569bd6-524d-42fe-bb9/serviceAccounts/big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/attribute.aws_role/events-api-eks-node-kafka-connect"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account_iam_member" "token_creator" {
  service_account_id = "projects/project-e8569bd6-524d-42fe-bb9/serviceAccounts/big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "principalSet://iam.googleapis.com/projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/attribute.aws_role/events-api-eks-node-kafka-connect"

  lifecycle {
    prevent_destroy = true
  }
}
