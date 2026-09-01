variable "name_prefix" {
  type = string
}

variable "repository_names" {
  type    = list(string)
  default = ["app", "streaming", "dbt", "realtime"]
}
