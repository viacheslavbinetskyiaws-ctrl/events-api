resource "aws_sns_topic" "dbt_build_alerts" {
  name = "events-api-dbt-build-alerts"
}

resource "aws_sns_topic_subscription" "dbt_build_alerts_email" {
  topic_arn = aws_sns_topic.dbt_build_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_notification_email
}
