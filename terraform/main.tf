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

# Dedicated least-privilege runtime identity: the service runs as this account,
# not the default compute service account. It holds only Cloud SQL Client, read
# access to its own database-url secret, and publish on its own risk-events topic.
resource "google_service_account" "cloud_run" {
  account_id   = "risk-engine-runtime"
  display_name = "Risk Engine Runtime"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_database_url" {
  secret_id = google_secret_manager_secret.risk_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

# risk-events is a shared topic owned by platform-infrastructure; the risk engine
# publishes risk.evaluated onto it for downstream services. Referenced here, not
# created here.
data "google_pubsub_topic" "risk_events" {
  name = "risk-events"
}

resource "google_pubsub_topic_iam_member" "cloud_run_publish" {
  topic  = data.google_pubsub_topic.risk_events.id
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
        value = data.google_pubsub_topic.risk_events.id
      }

      env {
        name  = "PUBSUB_PUSH_SA"
        value = google_service_account.pubsub_push.email
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

# A dedicated identity for the push subscription. Pub/Sub mints an OIDC token as
# this account and attaches it to each push, and the consumer endpoint verifies
# it, so only authenticated Pub/Sub deliveries can feed behavioural state.
resource "google_service_account" "pubsub_push" {
  account_id   = "risk-pubsub-push"
  display_name = "Risk Engine Pub/Sub Push"
}

# Pub/Sub's service agent must be allowed to mint tokens as the push identity.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
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

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
    }
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

# Keyless CI deploy: GitHub Actions authenticates via Workload Identity
# Federation and impersonates a dedicated least-privilege deploy service
# account, so no service-account key is stored in the repository. The pool and
# provider are shared and owned by platform-infrastructure; this repo references
# the pool (its name composed from the project number) and contributes only its
# own deploy account and binding.
locals {
  wif_pool_name = "projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/github-actions"
}

resource "google_service_account" "deploy" {
  account_id   = "risk-engine-deploy"
  display_name = "Risk Engine Deploy"
}

resource "google_project_iam_member" "deploy_roles" {
  for_each = toset([
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# Only the risk-engine repository may impersonate the deploy service account.
resource "google_service_account_iam_member" "deploy_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.wif_pool_name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}
