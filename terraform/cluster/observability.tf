# Lives with the cluster because the metric it watches is published by the
# dbt CronJob: with treat_missing_data = "breaching", a torn-down cluster
# would otherwise fire ALARM (and, on the next `up`, OK) emails. The SNS
# topic and its email subscription stay in the foundation root so the
# subscription is confirmed once, not on every `up`.
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
  alarm_actions       = [data.aws_sns_topic.dbt_build_alerts.arn]
  ok_actions          = [data.aws_sns_topic.dbt_build_alerts.arn]
}
