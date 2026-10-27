output "vm_ip" { value = google_compute_address.ip.address }
output "vm_service_account" { value = google_service_account.vm.email }
output "sql_private_ip" { value = google_sql_database_instance.pg.private_ip_address }
output "sql_instance" { value = google_sql_database_instance.pg.name }

# Lines for the cell's configuration (deploy/gcp/env.example); the key ring name matches ZID_GCP_KMS_KEY_RING
output "backend_settings" {
  value = {
    ZID_KEY_PROVIDER         = "gcp-kms"
    ZID_GCP_KMS_PROJECT      = var.project
    ZID_GCP_KMS_LOCATION     = var.location
    ZID_GCP_KMS_KEY_RING     = local.ring_name
    ZID_GCP_KMS_HSM          = var.protection_level == "HSM" ? "true" : "false"
    ZID_EVIDENCE_SIGNING_KID = "evidence-${var.cell_id}"
    ZID_WEBHOOK_KMS_KEY_ID   = "${google_kms_key_ring.cell.id}/cryptoKeys/webhook_signing"
    ZID_DIRECTORY_KMS_KEY_ID = "${google_kms_key_ring.cell.id}/cryptoKeys/directory_signing"
    ZID_DISCOVERY_KMS_KEY_ID = "${google_kms_key_ring.cell.id}/cryptoKeys/discovery_signing"
  }
}

# With the load balancer on, point id./api./global.<domain> at this address instead of vm_ip
output "lb_ip" {
  value = local.lb ? google_compute_global_address.lb[0].address : null
}
