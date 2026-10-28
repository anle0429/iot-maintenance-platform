# Fail closed across the approved cloud plan and authored application manifests before a deployment command.
package iot.deployment
import rego.v1

deny contains "unknown request format" if {
  object.get(input, "format", "") != "iot-deployment-request-v1"
}
deny contains "unknown deployment kind" if {
  not object.get(input, "kind", "") in {"terraform", "kubernetes"}
}
deny contains "Terraform resource inventory is empty" if {
  input.kind == "terraform"
  count(object.get(input, "resources", [])) == 0
}
deny contains sprintf("destructive action: %s", [r.address]) if {
  input.kind == "terraform"
  some r in input.resources
  "delete" in r.actions
}
deny contains "VM machine type exceeds the approved profile" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not r.values.machine_type == data.approved.machine_type
}
deny contains "VM must retain the approved eight-hour STOP limit" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not r.values.scheduling == {"max_run_seconds": data.approved.max_run_seconds, "termination_action": "STOP"}
}
deny contains "additional external address configurations exceed the approved inventory" if {
  input.kind == "terraform"
  some r in input.resources
  count(object.get(r.values, "access_configs", [])) > data.approved.max_access_configs
}
deny contains "VM network inventory must be explicit" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not is_array(object.get(r.values, "access_configs", null))
}
deny contains "VM network tier differs from the approved existing egress" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  some cfg in r.values.access_configs
  not cfg.network_tier == data.approved.network_tier
}
deny contains "VM network interface inventory differs" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not r.values.network_interface_count == data.approved.network_interface_count
}
deny contains "VM tags must retain the reviewed IAP firewall target" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not r.values.network_tags == data.approved.network_tags
}
deny contains "firewall may allow only the reviewed SSH rule" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_firewall"
  not r.values.allowed_rules == data.approved.ssh_allowed_rules
}
deny contains "firewall target tags differ from the reviewed VM" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_firewall"
  not r.values.target_tags == data.approved.network_tags
}
deny contains sprintf("unapproved disk size: %s", [r.values.name]) if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_disk"
  not data.approved.disk_sizes_gib[r.values.name] == r.values.size_gib
}
deny contains "SSH source ranges must stay IAP-only" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_firewall"
  not r.values.source_ranges == data.approved.ssh_source_ranges
}
deny contains "application must be a namespaced Deployment" if {
  input.kind == "kubernetes"
  not input.manifest.kind == "Deployment"
}
deny contains "application namespace is outside the policy scope" if {
  input.kind == "kubernetes"
  not object.get(input.manifest.metadata, "namespace", "") in {"iot-features", "iot-serving", "iot-mesh"}
}
pod := object.get(object.get(object.get(object.get(input, "manifest", {}), "spec", {}), "template", {}), "spec", {}) if input.kind == "kubernetes"
deny contains "application containers are missing" if {
  input.kind == "kubernetes"
  count(object.get(pod, "containers", [])) == 0
}
deny contains "application user must be positive" if {
  input.kind == "kubernetes"
  object.get(object.get(pod, "securityContext", {}), "runAsUser", 0) <= 0
}
deny contains "authored application init containers need a separate reviewed policy" if {
  input.kind == "kubernetes"
  count(object.get(pod, "initContainers", [])) > 0
}
deny contains "hostPath volumes are forbidden" if {
  input.kind == "kubernetes"
  some volume in object.get(pod, "volumes", [])
  object.get(volume, "hostPath", null) != null
}
deny contains "unapproved Terraform address" if {
  input.kind == "terraform"
  some r in input.resources
  not r.address in data.approved.resource_addresses
}
deny contains "Terraform address and resource type disagree" if {
  input.kind == "terraform"
  some r in input.resources
  not r.type == data.approved.resource_types[r.address]
}
deny contains "Terraform inventory must contain each approved address exactly once" if {
  input.kind == "terraform"
  count(input.resources) != count(data.approved.resource_addresses)
}
deny contains "Terraform inventory contains duplicate addresses" if {
  input.kind == "terraform"
  count({r.address | some r in input.resources}) != count(input.resources)
}
deny contains "Terraform must describe exactly one approved VM" if {
  input.kind == "terraform"
  count([r | some r in input.resources; r.type == "google_compute_instance"]) != 1
}
deny contains "Terraform must retain exactly two approved data disks" if {
  input.kind == "terraform"
  count([r | some r in input.resources; r.type == "google_compute_disk"]) != 2
}
deny contains "VM boot disk differs from the approved size and type" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not r.values.boot_disk == data.approved.boot_disk
}
deny contains "VM attached disk inventory differs" if {
  input.kind == "terraform"
  some r in input.resources
  r.type == "google_compute_instance"
  not sort(r.values.attached_disk_names) == sort(object.keys(data.approved.disk_sizes_gib))
}
deny contains "host networking is forbidden" if {
  input.kind == "kubernetes"
  object.get(pod, "hostNetwork", false)
}
deny contains "application must disable automatic service-account token mounting" if {
  input.kind == "kubernetes"
  object.get(pod, "automountServiceAccountToken", true) != false
}
deny contains "application must declare a nonroot user and RuntimeDefault seccomp" if {
  input.kind == "kubernetes"
  not pod.securityContext.runAsNonRoot == true
}
deny contains "application must declare RuntimeDefault seccomp" if {
  input.kind == "kubernetes"
  object.get(object.get(pod, "securityContext", {}), "seccompProfile", {}) != {"type": "RuntimeDefault"}
}
deny contains sprintf("unsafe privilege/capability settings: %s", [c.name]) if {
  input.kind == "kubernetes"
  some c in pod.containers
  not c.securityContext == {"readOnlyRootFilesystem": true, "allowPrivilegeEscalation": false, "capabilities": {"drop": ["ALL"]}}
}
deny contains sprintf("unapproved application CPU limit: %s", [c.name]) if {
  input.kind == "kubernetes"
  some c in pod.containers
  not object.get(object.get(c, "resources", {}), "limits", {}).cpu in data.approved.application_cpu_limits
}
deny contains sprintf("unapproved application memory limit: %s", [c.name]) if {
  input.kind == "kubernetes"
  some c in pod.containers
  not object.get(object.get(c, "resources", {}), "limits", {}).memory in data.approved.application_memory_limits
}
deny contains "model is not the approved production version" if {
  input.kind == "kubernetes"
  not input.model_pin.version == data.approved.model_version
}
deny contains "full model lineage differs from the approved release" if {
  input.kind == "kubernetes"
  not input.model_pin == data.approved.model_pin
}
deny contains "candidate model cannot be served as production" if {
  input.kind == "kubernetes"
  not input.model_pin.production == true
}
deny contains "portable model bytes do not match the approved lineage" if {
  input.kind == "kubernetes"
  not input.artifact_sha256 == input.model_pin.portable_sha256
}
deny contains "portable model is outside the approved release" if {
  input.kind == "kubernetes"
  not input.artifact_sha256 == data.approved.portable_sha256
}
deny contains "feature context and model protocol differ" if {
  input.kind == "kubernetes"
  not input.context_protocol_sha256 == input.model_pin.protocol_sha256
}
deny contains "feature protocol is outside the approved release" if {
  input.kind == "kubernetes"
  not input.context_protocol_sha256 == data.approved.protocol_sha256
}

result := {"allowed": count(deny) == 0, "violations": sort(deny)}
