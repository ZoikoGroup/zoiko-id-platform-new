# GCP platform for one Zoiko iD cell (DEC-F5 keys, DEC-G2 topology on GCP). Creates:
#   - a VM service account with only the roles it needs (KMS use, Artifact Registry read, log/metric write)
#   - a Cloud KMS key ring "zid-<cell>" with HSM-protected keys: data/erasure/backup KEKs, HMAC, evidence signing,
#     webhook signing, directory/discovery signing and one signing key per token purpose (token, set, federation,
#     credential, verifier), plus the OpenBao auto-unseal key in key ring "zid-seal"
#   - a private network and Cloud SQL for PostgreSQL 17 (private IP only, point-in-time recovery on); Valkey runs in
#     the compose stack
#   - the VM itself (Debian 12 with Docker, because the compose file needs docker compose)
# Key destruction is delayed (destroy_scheduled_duration) so a mistaken destroy can be reversed; crypto-erasure
# (DEC-F1) schedules destruction of the subject erasure key versions on purpose.

terraform {
  required_version = ">= 1.8"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

locals {
  ring_name = "zid-${var.cell_id}"

  # Symmetric keys (ENCRYPT_DECRYPT) and the MAC key, named like crypto_runtime.kek_ref builds them
  symmetric_keys = ["data_kek", "subject_erasure_key", "backup_kek", "secret_encryption"]
  signing_keys = [
    "evidence_signing",
    "webhook_signing",
    "directory_signing",
    "discovery_signing",
    "signing_token",
    "signing_set",
    "signing_federation",
    "signing_credential",
    "signing_verifier",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset([
    "cloudkms.googleapis.com", "compute.googleapis.com", "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com", "artifactregistry.googleapis.com", "secretmanager.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

resource "google_service_account" "vm" {
  account_id   = "zid-vm-${var.cell_id}"
  display_name = "Zoiko iD cell ${var.cell_id} VM"
}

resource "google_kms_key_ring" "cell" {
  name       = local.ring_name
  location   = var.location
  depends_on = [google_project_service.apis]
}

resource "google_kms_key_ring" "seal" {
  name       = "zid-seal"
  location   = var.location
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "symmetric" {
  for_each                   = toset(local.symmetric_keys)
  name                       = each.key
  key_ring                   = google_kms_key_ring.cell.id
  purpose                    = "ENCRYPT_DECRYPT"
  destroy_scheduled_duration = "${var.key_destroy_days * 24 * 3600}s"
  # The key is rotated every 90 days; Cloud KMS picks the right version on Decrypt, so old envelopes stay readable
  rotation_period = "7776000s"
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.protection_level
  }
  lifecycle { prevent_destroy = true }
}

resource "google_kms_crypto_key" "hmac" {
  name                       = "hmac_internal"
  key_ring                   = google_kms_key_ring.cell.id
  purpose                    = "MAC"
  destroy_scheduled_duration = "${var.key_destroy_days * 24 * 3600}s"
  version_template {
    algorithm        = "HMAC_SHA256"
    protection_level = "HSM"
  }
  lifecycle { prevent_destroy = true }
}

resource "google_kms_crypto_key" "signing" {
  for_each                   = toset(local.signing_keys)
  name                       = each.key
  key_ring                   = google_kms_key_ring.cell.id
  purpose                    = "ASYMMETRIC_SIGN"
  destroy_scheduled_duration = "${var.key_destroy_days * 24 * 3600}s"
  # No automatic rotation: a signing key rotation publishes a new kid, which is a reviewed operation (ZID-AUTH-001 §17)
  version_template {
    algorithm        = "EC_SIGN_P256_SHA256"
    protection_level = var.protection_level
  }
  lifecycle { prevent_destroy = true }
}

resource "google_kms_crypto_key" "openbao_unseal" {
  name     = "openbao-unseal"
  key_ring = google_kms_key_ring.seal.id
  purpose  = "ENCRYPT_DECRYPT"
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.protection_level
  }
  lifecycle { prevent_destroy = true }
}

# The service account may use keys, not administer them: no cloudkms.admin, and the destroy permission only on the
# subject erasure key.
resource "google_kms_crypto_key_iam_member" "encrypt" {
  for_each      = google_kms_crypto_key.symmetric
  crypto_key_id = each.value.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_kms_crypto_key_iam_member" "sign" {
  for_each      = google_kms_crypto_key.signing
  crypto_key_id = each.value.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_kms_crypto_key_iam_member" "mac" {
  crypto_key_id = google_kms_crypto_key.hmac.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_kms_crypto_key_iam_member" "unseal" {
  crypto_key_id = google_kms_crypto_key.openbao_unseal.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_custom_role" "erasure_destroy" {
  role_id     = "zidErasureKeyDestroy_${replace(var.cell_id, "-", "_")}"
  title       = "Zoiko iD crypto-erasure key destroy"
  permissions = ["cloudkms.cryptoKeyVersions.destroy"]
}

# Granted on the subject erasure key only (DEC-F1: destroying it is how a subject is erased)
resource "google_kms_crypto_key_iam_member" "erasure_destroy" {
  crypto_key_id = google_kms_crypto_key.symmetric["subject_erasure_key"].id
  role          = google_project_iam_custom_role.erasure_destroy.id
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_kms_crypto_key_iam_member" "viewer" {
  for_each      = merge(google_kms_crypto_key.symmetric, google_kms_crypto_key.signing)
  crypto_key_id = each.value.id
  role          = "roles/cloudkms.viewer" # list/get versions and public keys
  member        = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_member" "vm" {
  for_each = toset(["roles/artifactregistry.reader", "roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project
  role     = each.key
  member   = "serviceAccount:${google_service_account.vm.email}"
}

# --- network and Cloud SQL (private IP only) -------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  name                    = "zid-${var.cell_id}"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "vm" {
  name                     = "zid-${var.cell_id}-vm"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_global_address" "sql_range" {
  name          = "zid-${var.cell_id}-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_range.name]
}

resource "google_sql_database_instance" "pg" {
  name                = "zid-${var.cell_id}"
  region              = var.region
  database_version    = "POSTGRES_17"
  deletion_protection = true
  depends_on          = [google_service_networking_connection.sql]

  settings {
    tier              = var.sql_tier
    availability_type = var.sql_high_availability ? "REGIONAL" : "ZONAL"
    disk_autoresize   = true
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true # PITR (RES rows): 7 days of WAL
      transaction_log_retention_days = 7
      backup_retention_settings { retained_backups = 14 }
    }
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
  }
}

resource "google_sql_database" "cell" {
  name     = "zid_cell"
  instance = google_sql_database_instance.pg.name
}

# --- the VM ---------------------------------------------------------------------------------------------------

resource "google_compute_address" "ip" {
  name   = "zid-${var.cell_id}"
  region = var.region
}

resource "google_compute_firewall" "web" {
  count   = var.enable_load_balancer || length(var.direct_web_cidrs) > 0 ? 1 : 0
  name    = "zid-${var.cell_id}-web"
  network = google_compute_network.vpc.name
  allow {
    protocol = "tcp"
    ports    = var.enable_load_balancer ? ["80"] : ["80", "443"]
  }
  target_tags = ["zid"]
  # Only Google's load balancer ranges when the LB is on; otherwise the explicit list. The IaC policy
  # (tools/scripts/iac_policy.py) rejects 0.0.0.0/0, so a public site without the LB needs the load balancer.
  source_ranges = var.enable_load_balancer ? ["130.211.0.0/22", "35.191.0.0/16"] : var.direct_web_cidrs
}

resource "google_compute_firewall" "ssh" {
  name    = "zid-${var.cell_id}-ssh"
  network = google_compute_network.vpc.name
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  target_tags   = ["zid"]
  source_ranges = var.admin_cidrs # IAP range 35.235.240.0/20 for browser SSH, or your office IPs
}

resource "google_compute_instance" "vm" {
  name         = "zid-${var.cell_id}"
  zone         = var.zone
  machine_type = var.machine_type
  tags         = ["zid"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50
      type  = "pd-ssd"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.vm.id
    access_config { nat_ip = google_compute_address.ip.address }
  }
  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  metadata = {
    enable-oslogin = "TRUE"
  }
  metadata_startup_script = <<-EOT
    #!/bin/sh
    command -v docker >/dev/null || (apt-get update && apt-get install -y ca-certificates curl && curl -fsSL https://get.docker.com | sh)
  EOT
}
