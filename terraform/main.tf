terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {}

# The Cloud SQL instance is owned by the ledger. The risk engine takes its own
# database and user on it rather than standing up a second server.
data "google_sql_database_instance" "ledger_db" {
  name = var.sql_instance_name
}

# The orchestrator's topic the risk engine consumes payment events from to build
# behavioural state. Owned by the orchestrator; referenced here.
data "google_pubsub_topic" "payment_events" {
  name = var.payment_events_topic
}

resource "google_sql_database" "risk" {
  name     = "risk"
  instance = data.google_sql_database_instance.ledger_db.name
}

resource "google_sql_user" "risk" {
  name     = "risk"
  instance = data.google_sql_database_instance.ledger_db.name
  password = var.risk_db_password
}

resource "google_artifact_registry_repository" "risk_engine" {
  location      = var.region
  repository_id = "risk-engine"
  format        = "DOCKER"
}

# The full connection string is one secret, so Cloud Run never sees a plaintext
# DATABASE_URL. The socket path points at the shared Cloud SQL instance.
resource "google_secret_manager_secret" "risk_database_url" {
  secret_id = "risk-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "risk_database_url" {
  secret      = google_secret_manager_secret.risk_database_url.id
  secret_data = "postgresql://${google_sql_user.risk.name}:${var.risk_db_password}@/${google_sql_database.risk.name}?host=/cloudsql/${data.google_sql_database_instance.ledger_db.connection_name}"
}

resource "google_service_account" "cloud_run" {
  account_id   = "risk-engine-runner"
  display_name = "Risk Engine Cloud Run"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_database_url" {
  secret_id = google_secret_manager_secret.risk_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

# The risk engine's own topic: risk.evaluated, for downstream services.
resource "google_pubsub_topic" "risk_events" {
  name = "risk-events"
}

resource "google_pubsub_topic_iam_member" "cloud_run_publish" {
  topic  = google_pubsub_topic.risk_events.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_cloud_run_v2_service" "risk_engine" {
  name     = "risk-engine"
  location = var.region

  template {
    service_account = google_service_account.cloud_run.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/risk-engine/risk-engine:latest"

      ports {
        container_port = 8080
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.risk_database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }

      env {
        name  = "PUBSUB_TOPIC"
        value = google_pubsub_topic.risk_events.id
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [data.google_sql_database_instance.ledger_db.connection_name]
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.risk_engine.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Dead-letter for payment events the push endpoint cannot process, so a poison
# message is retried a bounded number of times and then parked, not looped.
resource "google_pubsub_topic" "dead_letter" {
  name = "risk-events-deadletter"
}

# A push subscription on the orchestrator's payment-events topic, delivering to
# the risk engine's consumer endpoint. This is the asynchronous state path: the
# engine builds behavioural state from these events, independently of the
# synchronous decision path.
resource "google_pubsub_subscription" "payment_events_to_risk" {
  name  = "payment-events-to-risk"
  topic = data.google_pubsub_topic.payment_events.id

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.risk_engine.uri}/events/pubsub"
  }

  ack_deadline_seconds = 20

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# Dead-lettering requires the Pub/Sub service agent to publish to the dead-letter
# topic and to acknowledge on the subscription.
resource "google_pubsub_topic_iam_member" "dead_letter_publish" {
  topic  = google_pubsub_topic.dead_letter.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscribe" {
  subscription = google_pubsub_subscription.payment_events_to_risk.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
