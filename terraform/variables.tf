variable "project_id" {
  description = "GCP project ID. The risk engine shares the ledger's project."
  type        = string
  default     = "ledger-api-507618"
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "europe-west2"
}

variable "sql_instance_name" {
  description = "Name of the ledger's Cloud SQL instance the risk engine takes its database on"
  type        = string
  default     = "ledger-db"
}

variable "payment_events_topic" {
  description = "The orchestrator's topic the risk engine consumes payment events from"
  type        = string
  default     = "payment-events"
}

variable "risk_db_password" {
  description = "Password for the risk engine's database user. Stored in Secret Manager as part of the connection string and injected into Cloud Run, never set as a plaintext env var."
  type        = string
  sensitive   = true
}
