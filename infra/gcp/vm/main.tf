# Provision a single Linux VM with IAP-only SSH; this file does not deploy coursework services.
locals {
  labels   = { project = "edai-k11", workload = "iot-coursework", managed_by = "terraform" }
  profiles = jsondecode(file("${path.module}/../capacity-profiles.json"))
  capacity = lookup(local.profiles, var.profile, local.profiles.basic)
}

resource "google_project_service" "required" {
  for_each           = toset(["compute.googleapis.com", "iam.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "coursework" {
  name                    = "${var.name_prefix}-network"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "coursework" {
  name                     = "${var.name_prefix}-subnet"
  ip_cidr_range            = "10.42.0.0/24"
  region                   = var.region
  network                  = google_compute_network.coursework.id
  private_ip_google_access = true
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "${var.name_prefix}-iap-ssh"
  network       = google_compute_network.coursework.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["${var.name_prefix}-iap"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_service_account" "workload" {
  account_id   = "${var.name_prefix}-vm"
  display_name = "Coursework VM identity, no project data permissions granted"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "operator" {
  for_each   = toset(["roles/iap.tunnelResourceAccessor", "roles/compute.osAdminLogin"])
  project    = var.project_id
  role       = each.value
  member     = var.operator_member
  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "operator" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.serviceAccountUser"
  member             = var.operator_member
}

resource "google_compute_disk" "data" {
  name   = "${var.name_prefix}-data"
  zone   = var.zone
  type   = "pd-standard"
  size   = var.data_disk_gib
  labels = local.labels
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}

resource "google_compute_disk" "scratch" {
  count      = local.capacity.scratch_gib > 0 ? 1 : 0
  name       = "${var.name_prefix}-scratch"
  zone       = var.zone
  type       = local.capacity.scratch_type
  size       = local.capacity.scratch_gib
  labels     = local.labels
  depends_on = [google_project_service.required]
}

resource "google_compute_instance" "coursework" {
  name                      = "${var.name_prefix}-vm"
  machine_type              = local.capacity.machine_type
  zone                      = var.zone
  allow_stopping_for_update = true
  labels                    = local.labels
  tags                      = ["${var.name_prefix}-iap"]
  boot_disk {
    auto_delete = true
    initialize_params {
      image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
      size  = local.capacity.boot_gib
      type  = local.capacity.boot_type
    }
  }
  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "coursework-data"
    mode        = "READ_WRITE"
  }
  dynamic "attached_disk" {
    for_each = google_compute_disk.scratch
    content {
      source      = attached_disk.value.id
      device_name = "coursework-scratch"
      mode        = "READ_WRITE"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.coursework.id
    # Ephemeral IPv4 allows package/image downloads without a NAT gateway.
    # Ingress is still limited to IAP SSH by the sole firewall rule above.
    access_config {
      network_tier = "STANDARD"
    }
  }
  metadata = {
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
  }
  service_account {
    email  = google_service_account.workload.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  scheduling {
    provisioning_model          = "STANDARD"
    automatic_restart           = false
    on_host_maintenance         = "MIGRATE"
    instance_termination_action = "STOP"
    max_run_duration {
      seconds = var.session_hours * 3600
    }
  }
  depends_on = [google_compute_firewall.iap_ssh, google_project_iam_member.operator, google_service_account_iam_member.operator]
}
