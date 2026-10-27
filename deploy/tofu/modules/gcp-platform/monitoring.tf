# Uptime checks and alerts (OPS/SRE readiness), and daily snapshots of the VM disk (OpenBao data, Caddy certificates).

locals {
  alerting = var.domain != "" && var.alert_email != ""
  hosts    = local.alerting ? { id = "id.${var.domain}", api = "api.${var.domain}", global = "global.${var.domain}" } : {}
  paths    = { id = "/sign-in", api = "/health/ready", global = "/health/ready" }
}

resource "google_monitoring_notification_channel" "email" {
  count        = local.alerting ? 1 : 0
  display_name = "Zoiko iD ${var.cell_id} on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_uptime_check_config" "https" {
  for_each     = local.hosts
  display_name = "zid-${var.cell_id}-${each.key}"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = local.paths[each.key]
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project, host = each.value }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  count        = local.alerting ? 1 : 0
  display_name = "zid-${var.cell_id} endpoint down"
  combiner     = "OR"
  conditions {
    display_name = "An uptime check fails for 3 minutes"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "180s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email[0].id]
  depends_on            = [google_monitoring_uptime_check_config.https]
}

resource "google_monitoring_alert_policy" "sql_disk" {
  count        = local.alerting ? 1 : 0
  display_name = "zid-${var.cell_id} database disk above 80%"
  combiner     = "OR"
  conditions {
    display_name = "Cloud SQL disk utilization"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\" AND resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project}:${google_sql_database_instance.pg.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "300s"
    }
  }
  notification_channels = [google_monitoring_notification_channel.email[0].id]
}

resource "google_compute_resource_policy" "snapshots" {
  name   = "zid-${var.cell_id}-daily"
  region = var.region
  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "02:00"
      }
    }
    retention_policy {
      max_retention_days    = 14
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "boot" {
  name = google_compute_resource_policy.snapshots.name
  disk = google_compute_instance.vm.name
  zone = var.zone
}
