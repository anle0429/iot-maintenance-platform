# Pin the provider used to prepare and later provision one coursework VM.
terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.8.1"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
