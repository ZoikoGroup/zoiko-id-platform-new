# Two production cells on GCP, one project per cell (blast radius and residency evidence, as the AWS account-per-cell
# model in DEC-G2). Each cell is one instantiation of the gcp-platform module with its own provider, state prefix,
# KMS key ring, Cloud SQL instance, VM and (optionally) load balancer. Cell ids must match backend/config/cells/cells.yaml.
#
#   tofu init && tofu plan -var-file=prod.tfvars
terraform {
  required_version = ">= 1.8"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
  # State in a bucket that lives outside the cells, versioned and with object retention:
  #   gsutil mb -l europe-west2 gs://<org>-zid-tofu-state && gsutil versioning set on gs://<org>-zid-tofu-state
  # backend "gcs" { bucket = "<org>-zid-tofu-state", prefix = "gcp-prod" }
}

variable "domain_uk" { type = string }
variable "domain_eu" { type = string }
variable "alert_email" { type = string }
variable "admin_cidrs" { type = list(string) }
variable "project_uk" { type = string }
variable "project_eu" { type = string }

provider "google" {
  alias   = "uk"
  project = var.project_uk
  region  = "europe-west2" # London
}

provider "google" {
  alias   = "eu"
  project = var.project_eu
  region  = "europe-west4" # Netherlands
}

module "cell_uk_1" {
  source    = "../../modules/gcp-platform"
  providers = { google = google.uk }

  project              = var.project_uk
  cell_id              = "uk-1"
  region               = "europe-west2"
  zone                 = "europe-west2-a"
  location             = "europe-west2"
  domain               = var.domain_uk
  alert_email          = var.alert_email
  admin_cidrs          = var.admin_cidrs
  enable_load_balancer = true
}

module "cell_eu_1" {
  source    = "../../modules/gcp-platform"
  providers = { google = google.eu }

  project              = var.project_eu
  cell_id              = "eu-1"
  region               = "europe-west4"
  zone                 = "europe-west4-a"
  location             = "europe-west4"
  domain               = var.domain_eu
  alert_email          = var.alert_email
  admin_cidrs          = var.admin_cidrs
  enable_load_balancer = true
}

output "uk_1" {
  value = {
    lb_ip    = module.cell_uk_1.lb_ip
    settings = module.cell_uk_1.backend_settings
  }
}

output "eu_1" {
  value = {
    lb_ip    = module.cell_eu_1.lb_ip
    settings = module.cell_eu_1.backend_settings
  }
}
