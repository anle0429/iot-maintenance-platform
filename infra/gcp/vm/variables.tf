# Bound the initial cloud footprint and require explicit project/operator identity.
variable "project_id" {
  description = "Existing coursework project ID; configure locally, not in committed files."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Use a valid Google Cloud project ID, not its display name or numeric project number."
  }
}

variable "operator_member" {
  description = "Human principal for IAP/OS Login, such as user:learner@example.com."
  type        = string
  validation {
    condition     = can(regex("^user:[^ @]+@[^ @]+$", var.operator_member))
    error_message = "Use one explicit human user principal; groups and public access are not supported here."
  }
}

variable "name_prefix" {
  description = "Unique coursework prefix for resources in the existing project."
  type        = string
  default     = "iot-cw"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,18}[a-z0-9]$", var.name_prefix))
    error_message = "Choose 4-20 lowercase letters/digits/hyphens, beginning with a letter and ending alphanumeric."
  }
}

variable "region" {
  description = "Initial pricing/placement scope; changing region requires a fresh cost review."
  type        = string
  default     = "us-central1"
  validation {
    condition     = var.region == "us-central1"
    error_message = "This first module/cost estimate is scoped to us-central1."
  }
}

variable "zone" {
  description = "Zone subject to actual project quota and machine availability."
  type        = string
  default     = "us-central1-a"
  validation {
    condition     = contains(["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"], var.zone)
    error_message = "Choose a us-central1 zone; preflight must verify actual availability."
  }
}

variable "profile" {
  description = "Reviewed capacity profile; a saved plan/cost review is required before applying a larger profile."
  type        = string
  default     = "basic"
  validation {
    condition     = contains(["basic", "standard", "accelerated"], var.profile)
    error_message = "Use a reviewed basic, standard or accelerated profile."
  }
}

variable "data_disk_gib" {
  description = "Separate retained data disk; current 100GB budget is an estimate, not a completed workload."
  type        = number
  default     = 500
  validation {
    condition     = var.data_disk_gib == 500
    error_message = "Initial reviewed footprint uses exactly 500 GiB; review cost/capacity before changing it."
  }
}

variable "session_hours" {
  description = "Maximum runtime per start before GCE stops the VM; retained disks still incur charges."
  type        = number
  default     = 8
  validation {
    condition     = var.session_hours >= 1 && var.session_hours <= 12 && floor(var.session_hours) == var.session_hours
    error_message = "Use an integer runtime from 1 to 12 hours."
  }
}
