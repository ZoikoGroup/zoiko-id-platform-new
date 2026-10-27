# Optional global external HTTPS load balancer with Google-managed certificates and a Cloud Armor policy.
# Traffic: client -> LB (TLS, WAF, rate limit) -> VM port 80 (Caddy, deploy/gcp/Caddyfile.lb) -> containers.

locals {
  lb       = var.enable_load_balancer
  lb_hosts = local.lb ? ["id.${var.domain}", "api.${var.domain}", "global.${var.domain}"] : []
}

check "lb_needs_domain" {
  assert {
    condition     = !var.enable_load_balancer || var.domain != ""
    error_message = "enable_load_balancer needs `domain`."
  }
}

resource "google_compute_global_address" "lb" {
  count = local.lb ? 1 : 0
  name  = "zid-${var.cell_id}-lb"
}

resource "google_compute_managed_ssl_certificate" "lb" {
  count = local.lb ? 1 : 0
  name  = "zid-${var.cell_id}-lb"
  managed {
    domains = local.lb_hosts
  }
}

resource "google_compute_instance_group" "vm" {
  count     = local.lb ? 1 : 0
  name      = "zid-${var.cell_id}"
  zone      = var.zone
  instances = [google_compute_instance.vm.id]
  named_port {
    name = "http"
    port = 80
  }
}

resource "google_compute_health_check" "lb" {
  count               = local.lb ? 1 : 0
  name                = "zid-${var.cell_id}-lb"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3
  http_health_check {
    port         = 80
    request_path = "/lb-health" # answered by Caddy itself, so a slow app does not drain the VM
  }
}

# WAF and per-IP rate limit (OWASP preconfigured rules at sensitivity 1 to keep false positives low)
resource "google_compute_security_policy" "armor" {
  count = local.lb ? 1 : 0
  name  = "zid-${var.cell_id}-armor"

  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
    description = "SQL injection"
  }
  rule {
    action   = "deny(403)"
    priority = 1001
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
    description = "Cross-site scripting"
  }
  rule {
    action   = "deny(403)"
    priority = 1002
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('rce-v33-stable', {'sensitivity': 1})"
      }
    }
    description = "Local file inclusion and remote code execution"
  }
  rule {
    action   = "throttle"
    priority = 2000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = var.armor_rate_limit_per_minute
        interval_sec = 60
      }
    }
    description = "Per-IP rate limit"
  }
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow"
  }
}

resource "google_compute_backend_service" "lb" {
  count                 = local.lb ? 1 : 0
  name                  = "zid-${var.cell_id}"
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.lb[0].id]
  security_policy       = google_compute_security_policy.armor[0].id
  backend {
    group           = google_compute_instance_group.vm[0].id
    balancing_mode  = "UTILIZATION"
    max_utilization = 0.8
  }
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "https" {
  count           = local.lb ? 1 : 0
  name            = "zid-${var.cell_id}-https"
  default_service = google_compute_backend_service.lb[0].id
}

resource "google_compute_url_map" "redirect" {
  count = local.lb ? 1 : 0
  name  = "zid-${var.cell_id}-redirect"
  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_ssl_policy" "modern" {
  count           = local.lb ? 1 : 0
  name            = "zid-${var.cell_id}-modern"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "lb" {
  count            = local.lb ? 1 : 0
  name             = "zid-${var.cell_id}"
  url_map          = google_compute_url_map.https[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.lb[0].id]
  ssl_policy       = google_compute_ssl_policy.modern[0].id
}

resource "google_compute_target_http_proxy" "redirect" {
  count   = local.lb ? 1 : 0
  name    = "zid-${var.cell_id}-redirect"
  url_map = google_compute_url_map.redirect[0].id
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = local.lb ? 1 : 0
  name                  = "zid-${var.cell_id}-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.lb[0].address
  port_range            = "443"
  target                = google_compute_target_https_proxy.lb[0].id
}

resource "google_compute_global_forwarding_rule" "http" {
  count                 = local.lb ? 1 : 0
  name                  = "zid-${var.cell_id}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.lb[0].address
  port_range            = "80"
  target                = google_compute_target_http_proxy.redirect[0].id
}
