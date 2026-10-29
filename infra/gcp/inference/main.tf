# Add one bounded GPU worker to the existing private lab network; keep the data/control VM unchanged.
terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    google = { source = "hashicorp/google", version = "8.8.1" }
  }
}

variable "project_id" {
  description = "Existing authorized coursework project."
  type        = string
}

variable "zone" {
  description = "A reviewed US GPU zone; alternative regions are capacity fallbacks."
  type        = string
  default     = "us-central1-a"
  validation {
    condition     = contains(["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f", "us-east1-b", "us-east1-c", "us-east4-a", "us-east4-c", "us-west1-a", "us-west1-b"], var.zone)
    error_message = "Use a reviewed GPU zone in Iowa, South Carolina, Northern Virginia or Oregon."
  }
}

locals {
  region = join("-", slice(split("-", var.zone), 0, 2))
  subnet = local.region == "us-central1" ? data.google_compute_instance.control.network_interface[0].subnetwork : google_compute_subnetwork.gpu[0].self_link
}

variable "gpu_profile" {
  description = "Select one GPU; the eight-CPU L4 shape requires a separately verified 16-vCPU project quota."
  type        = string
  default     = "v100"
  validation {
    condition     = contains(["l4", "l4-8", "t4", "v100"], var.gpu_profile)
    error_message = "Use a reviewed single-GPU profile."
  }
}

provider "google" {
  project = var.project_id
  region  = local.region
  zone    = var.zone
}

data "google_compute_instance" "control" {
  name = "iot-cw-vm"
  zone = "us-central1-a"
}

data "google_compute_image" "ubuntu" {
  project = "ubuntu-os-cloud"
  family  = "ubuntu-2404-lts-amd64"
}

resource "google_compute_subnetwork" "gpu" {
  count                    = local.region == "us-central1" ? 0 : 1
  name                     = "iot-cw-gpu-subnet"
  ip_cidr_range            = "10.44.0.0/24"
  region                   = local.region
  network                  = data.google_compute_instance.control.network_interface[0].network
  private_ip_google_access = true
}

resource "google_compute_instance" "inference" {
  name         = "iot-cw-gpu"
  machine_type = var.gpu_profile == "l4-8" ? "g2-standard-8" : (var.gpu_profile == "l4" ? "g2-standard-4" : "n1-standard-4")
  zone         = var.zone
  labels       = { project = "edai-k11", workload = "llm-inference", managed_by = "terraform" }
  tags         = ["iot-cw-iap", "iot-cw-gpu"]
  boot_disk {
    auto_delete = true
    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      size  = 50
      type  = "pd-ssd"
    }
  }
  guest_accelerator {
    type  = var.gpu_profile == "v100" ? "nvidia-tesla-v100" : (var.gpu_profile == "t4" ? "nvidia-tesla-t4" : "nvidia-l4")
    count = 1
  }
  network_interface {
    subnetwork = local.subnet
  }
  metadata = {
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
  }
  service_account {
    email  = data.google_compute_instance.control.service_account[0].email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  shielded_instance_config {
    # The separately installed NVIDIA kernel module must load; retain vTPM and integrity monitoring.
    enable_secure_boot          = false
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  scheduling {
    provisioning_model          = "STANDARD"
    automatic_restart           = false
    on_host_maintenance         = "TERMINATE"
    instance_termination_action = "STOP"
    max_run_duration { seconds = 28800 }
  }
}

resource "google_compute_router" "egress" {
  name    = "iot-cw-gpu-egress"
  region  = local.region
  network = data.google_compute_instance.control.network_interface[0].network
}

resource "google_compute_router_nat" "egress" {
  name                               = "iot-cw-gpu-egress"
  router                             = google_compute_router.egress.name
  region                             = local.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = local.subnet
    source_ip_ranges_to_nat = ["PRIMARY_IP_RANGE"]
  }
}

resource "google_compute_firewall" "cluster_link" {
  name          = "iot-cw-gpu-private-cluster"
  network       = data.google_compute_instance.control.network_interface[0].network
  direction     = "INGRESS"
  source_ranges = ["${data.google_compute_instance.control.network_interface[0].network_ip}/32", "${google_compute_instance.inference.network_interface[0].network_ip}/32"]
  target_tags   = ["iot-cw-iap", "iot-cw-gpu"]
  allow {
    protocol = "tcp"
    ports    = ["6443", "10250"]
  }
  allow {
    protocol = "udp"
    ports    = ["8472"]
  }
}

output "worker" {
  description = "Public operational metadata; no Kubernetes join token or model credentials."
  value = {
    name                    = google_compute_instance.inference.name
    zone                    = google_compute_instance.inference.zone
    ip                      = google_compute_instance.inference.network_interface[0].network_ip
    maximum_session_seconds = 28800
  }
}
