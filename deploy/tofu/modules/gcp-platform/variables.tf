variable "project" { type = string }
variable "cell_id" {
  type        = string
  description = "ZID_CELL_ID, e.g. eu-1. Used in key ring and resource names."
}
variable "region" {
  type    = string
  default = "europe-west2"
}
variable "zone" {
  type    = string
  default = "europe-west2-a"
}
variable "location" {
  type        = string
  default     = "europe-west2"
  description = "Cloud KMS location. Keep it in the cell's residency region (ZID-TENANCY-001)."
}
variable "protection_level" {
  type        = string
  default     = "HSM"
  description = "HSM (FIPS 140-2 Level 3, set ZID_GCP_KMS_HSM=true) or SOFTWARE for non-production."
}
variable "key_destroy_days" {
  type        = number
  default     = 30
  description = "Pending window before a destroyed key version is gone for good (Cloud KMS minimum is 1 day)."
}
variable "machine_type" {
  type    = string
  default = "e2-standard-4"
}
variable "sql_tier" {
  type    = string
  default = "db-custom-2-8192"
}
variable "sql_high_availability" {
  type    = bool
  default = true
}
variable "admin_cidrs" {
  type        = list(string)
  description = "Source ranges allowed to SSH. Use 35.235.240.0/20 for IAP, never 0.0.0.0/0."
}
variable "domain" {
  type        = string
  default     = ""
  description = "Base domain (ZID_DOMAIN). When set, uptime checks watch id./api./global.<domain>."
}
variable "alert_email" {
  type        = string
  default     = ""
  description = "Email for uptime and database alerts. Empty disables the alert policies."
}
variable "enable_load_balancer" {
  type        = bool
  default     = false
  description = "Put a global external HTTPS load balancer with Google-managed certificates and Cloud Armor in front of the VM. Needs `domain`. Use deploy/gcp/Caddyfile.lb on the VM."
}
variable "armor_rate_limit_per_minute" {
  type        = number
  default     = 600
  description = "Cloud Armor per-client-IP request limit; above it clients get 429."
}
variable "direct_web_cidrs" {
  type        = list(string)
  default     = []
  description = "Source ranges allowed to reach the VM's ports 80/443 directly (an office or partner allow-list). Empty means no direct access; the load balancer ranges are used when it is enabled. 0.0.0.0/0 is rejected by the IaC policy: use the load balancer for a public site."
}
