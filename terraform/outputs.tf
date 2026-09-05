output "cloud_run_url" {
  description = "Live risk engine URL"
  value       = google_cloud_run_v2_service.risk_engine.uri
}

output "artifact_registry" {
  description = "Docker image registry path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/risk-engine"
}

output "risk_topic" {
  description = "Topic the engine publishes risk decisions to"
  value       = google_pubsub_topic.risk_events.id
}

output "payment_events_subscription" {
  description = "Push subscription feeding payment events into the engine"
  value       = google_pubsub_subscription.payment_events_to_risk.name
}

output "risk_database" {
  description = "Risk engine database on the shared Cloud SQL instance"
  value       = google_sql_database.risk.name
}
