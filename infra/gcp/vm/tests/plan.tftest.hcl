# Exercise plan constraints locally with mocked provider calls; creates no Google Cloud resources.
mock_provider "google" {}

variables {
  project_id      = "iot-test-project"
  operator_member = "user:learner@example.com"
}

run "bounded_vm_plan" {
  command = plan
  assert {
    condition     = google_compute_instance.coursework.machine_type == "e2-standard-4" && google_compute_disk.data.size == 500 && google_compute_disk.data.type == "pd-standard"
    error_message = "The initial VM/disk footprint must match the reviewed capacity."
  }
  assert {
    condition     = google_compute_instance.coursework.scheduling[0].max_run_duration[0].seconds == 28800 && google_compute_instance.coursework.scheduling[0].instance_termination_action == "STOP"
    error_message = "An eight-hour default session must stop, not delete, the VM."
  }
  assert {
    condition     = toset(google_compute_firewall.iap_ssh.source_ranges) == toset(["35.235.240.0/20"]) && toset(one(google_compute_firewall.iap_ssh.allow).ports) == toset(["22"])
    error_message = "The only ingress rule must restrict SSH to Google's IAP range."
  }
  assert {
    condition     = google_compute_instance.coursework.metadata["enable-oslogin"] == "TRUE" && google_compute_instance.coursework.shielded_instance_config[0].enable_secure_boot
    error_message = "OS Login and Secure Boot must be enabled in the plan."
  }
  assert {
    condition     = length(google_compute_instance.coursework.network_interface[0].access_config) == 1 && google_compute_instance.coursework.network_interface[0].access_config[0].network_tier == "STANDARD"
    error_message = "The estimate includes exactly one ephemeral Standard-tier IPv4."
  }
}

run "standard_profile_plan" {
  command = plan
  variables { profile = "standard" }
  assert {
    condition     = google_compute_instance.coursework.machine_type == "e2-standard-8" && google_compute_disk.scratch[0].size == 150 && google_compute_disk.scratch[0].type == "pd-balanced"
    error_message = "The standard profile must pair 8 vCPU/32 GiB with SSD scratch."
  }
}

run "accelerated_profile_plan" {
  command = plan
  variables { profile = "accelerated" }
  assert {
    condition     = google_compute_instance.coursework.machine_type == "e2-highmem-8" && google_compute_disk.scratch[0].size + google_compute_instance.coursework.boot_disk[0].initialize_params[0].size == 200 && length(google_compute_instance.coursework.attached_disk) == 2
    error_message = "The accelerated profile needs 64 GiB, two data/scratch attachments and a 200 GiB SSD footprint."
  }
}

run "reject_unreviewed_profile" {
  command = plan
  variables { profile = "unreviewed-16cpu" }
  expect_failures = [var.profile]
}

run "reject_public_operator" {
  command = plan
  variables { operator_member = "allUsers" }
  expect_failures = [var.operator_member]
}

run "reject_unbounded_session" {
  command = plan
  variables { session_hours = 24 }
  expect_failures = [var.session_hours]
}

run "reject_unreviewed_disk" {
  command = plan
  variables { data_disk_gib = 1000 }
  expect_failures = [var.data_disk_gib]
}
