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

The consumer endpoint is authenticated; the read and decision endpoints are open by scope decision. `POST /events/pubsub` changes the behavioural state that decisions read, so it is protected: the push subscription attaches a Google OIDC token minted for a dedicated push service account, and the endpoint verifies that token and the account it was issued to before applying an event, so an anonymous caller cannot post forged events to skew state. `GET /health`, `/docs` and `POST /risk/evaluate` stay public, a deliberate scope decision for a portfolio, and safe because the engine never moves money and its decision is advisory (an unreachable or wrong-holding engine causes the orchestrator to hold a payment, never to move it).

## Known limitations

- No authentication on the evaluate endpoint. In production the orchestrator would authenticate to the engine; today an evaluate request has no financial effect.
- The watch list behind DESTINATION_REPUTATION is a fixed synthetic set; a production list would be maintained and versioned from an authoritative source.
- SHORT_HISTORY measures observed history, not authoritative account age, until ABS emits an `account.created` fact.
- No rate limiting. Would be added via Cloud Armor or middleware.
- Encryption at rest relies on the managed encryption provided by Cloud SQL. Customer-managed keys are outside project scope.
