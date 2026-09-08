resource "aws_sns_topic" "dbt_build_alerts" {
  name = "events-api-dbt-build-alerts"
}

resource "aws_sns_topic_subscription" "dbt_build_alerts_email" {
  topic_arn = aws_sns_topic.dbt_build_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_notification_email
}

resource "aws_cloudwatch_metric_alarm" "dbt_build_failed" {
  alarm_name          = "events-api-dbt-build-failed"
  alarm_description   = "dbt CronJob build failed, or didn't run at all this hour"
  namespace           = "EventsApi/DataQuality"
  metric_name         = "DbtBuildPassed"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.dbt_build_alerts.arn]
  ok_actions          = [aws_sns_topic.dbt_build_alerts.arn]
}
