# ===================================
# CAOS Agent - Terraform Main
# Cloud Run + Pub/Sub + IAM
# ===================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "vivaiot-terraform-state"
    prefix = "caos-agent"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ===================================
# Variables
# ===================================

variable "project_id" {
  description = "GCP Project ID"
  type        = string
  default     = "vivaiot-prod"
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "caos-agent"
}

variable "image" {
  description = "Container image URI"
  type        = string
}

# ===================================
# Service Account
# ===================================

resource "google_service_account" "caos" {
  account_id   = "caos-agent-sa"
  display_name = "CAOS Agent Service Account"
  description  = "Service account for CAOS Agent Cloud Run service"
}

resource "google_project_iam_member" "caos_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_service_account.caos.email}"
}

resource "google_project_iam_member" "caos_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.caos.email}"
}

resource "google_project_iam_member" "caos_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.caos.email}"
}

resource "google_project_iam_member" "caos_workflows" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.caos.email}"
}

# ===================================
# Pub/Sub Topics & Subscriptions
# ===================================

resource "google_pubsub_topic" "sentinel_alerts" {
  name = "sentinel.alerts"
  message_retention_duration = "86400s"
}

resource "google_pubsub_topic" "oracle_predictions" {
  name = "oracle.predictions"
  message_retention_duration = "86400s"
}

resource "google_pubsub_topic" "caos_actions" {
  name = "caos.actions"
  message_retention_duration = "86400s"
}

resource "google_pubsub_subscription" "sentinel_sub" {
  name  = "sentinel.alerts-sub"
  topic = google_pubsub_topic.sentinel_alerts.id

  ack_deadline_seconds = 30
  message_retention_duration = "600s"

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.caos.uri}/v1/events/trigger"

    oidc_token {
      service_account_email = google_service_account.caos.email
    }
  }
}

resource "google_pubsub_subscription" "oracle_sub" {
  name  = "oracle.predictions-sub"
  topic = google_pubsub_topic.oracle_predictions.id

  ack_deadline_seconds = 30
  message_retention_duration = "600s"
}

# ===================================
# Cloud Run Service
# ===================================

resource "google_cloud_run_v2_service" "caos" {
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.caos.email

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_AI_LOCATION"
        value = var.region
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# ===================================
# Outputs
# ===================================

output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.caos.uri
}

output "service_account_email" {
  description = "Service account email"
  value       = google_service_account.caos.email
}
