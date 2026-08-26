
variable "use_locastack" {
  type    = bool
  default = true
}

variable "budget_notification_email" {
  type      = string
  sensitive = true
}
