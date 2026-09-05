# Security

## What this project is

A portfolio demonstration of payment risk decisioning and cloud engineering. It is not a production risk system, uses a synthetic watch list and simulated event volume, and handles no real personal or financial data.

## Boundaries

**Secrets management.** The database connection string is held in GCP Secret Manager, provisioned via Terraform, and injected into Cloud Run at runtime, never as a plaintext env var and never committed. `.gitignore` excludes `.env` and Terraform state.

**Keyless CI.** The deploy pipeline authenticates to GCP through Workload Identity Federation: GitHub Actions presents a short-lived OIDC token that GCP exchanges to impersonate a dedicated, repository-scoped deploy service account. No long-lived service-account key is stored in the repository, which removes the most common cloud-credential leak.

**No access to financial state.** The engine never reads or writes the ledger or the orchestrator's databases. It holds no ledger credential, so a compromise of the engine cannot move money; its blast radius is its own decisions and behavioural state.

**Input validation.** All requests are validated through Pydantic before the service layer. A non-positive amount, a malformed UUID or a missing field is rejected.

**Idempotency and replay.** Evaluation is idempotent on the caller-supplied `evaluation_id`, and consumed events are deduplicated on `event_id`, so a retried decision or a redelivered event has no extra effect.

**SQL injection.** All database access is through the SQLAlchemy ORM with parameterised queries. No raw string interpolation.

**Identifiers.** Decisions, payments and accounts use UUIDs; no sequential IDs are exposed.

**Error responses and logging.** Structured JSON errors with no stack traces; structured logs record metadata (payment id, decision), not request bodies.

**HTTPS.** Terminated by Cloud Run. The application does not handle TLS.

## Authentication

The engine's endpoints are unauthenticated, a deliberate scope decision for a portfolio, matching the ledger and orchestrator. The Pub/Sub push subscription delivers unauthenticated to the consumer endpoint, and the evaluate endpoint is open. Because the engine never moves money and its decision is advisory (an unreachable or wrong-holding engine causes the orchestrator to hold a payment, never to move it), the effect of an unauthenticated caller is bounded.

## Known limitations

- No OIDC verification on the push endpoint, so a forged event could be submitted to skew behavioural state. In production the push would carry a verified Pub/Sub OIDC token, and the effect is bounded today because state only informs a score and never moves money.
- No authentication on the evaluate endpoint. In production the orchestrator would authenticate to the engine.
- The watch list behind DESTINATION_REPUTATION is a fixed synthetic set; a production list would be maintained and versioned from an authoritative source.
- SHORT_HISTORY measures observed history, not authoritative account age, until ABS emits an `account.created` fact.
- No rate limiting. Would be added via Cloud Armor or middleware.
- Encryption at rest relies on the managed encryption provided by Cloud SQL. Customer-managed keys are outside project scope.
