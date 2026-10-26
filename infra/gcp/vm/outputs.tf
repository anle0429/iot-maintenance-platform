# Show operator commands and capacities without exporting credentials or a service endpoint.
output "vm_name" {
  description = "Name for gcloud operations after an approved apply."
  value       = google_compute_instance.coursework.name
}

output "connection_command" {
  description = "SSH through IAP and OS Login; no application port is made public."
  value       = "gcloud compute ssh ${google_compute_instance.coursework.name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "data_disk_bytes_before_filesystem" {
  description = "Provisioned bytes; filesystem overhead and actual workload space must still be checked on the VM."
  value       = var.data_disk_gib * pow(1024, 3)
}

output "session_limit_hours" {
  description = "GCE runtime stop limit; this does not cap disk/network or billing-account spend."
  value       = var.session_hours
}

output "capacity_profile" {
  description = "Reviewed hardware and suggested Spark settings; suggestions are not automatically deployed services."
  value       = local.capacity
}
