# MVP Scope

## Objective

Build and deploy a cloud-hosted risk engine that gives the payment orchestrator an immediate, explainable, deterministic decision on every payment, and that fails towards holding a payment rather than moving money.

## Must-have

- A synchronous decision endpoint, `POST /risk/evaluate`, returning allow, review or block with a score and reasons
- A deterministic weighted rules engine over payment and behavioural signals
- Configurable weights, thresholds and band boundaries, versioned so decisions stay replayable
- An append-only decision log recording the input snapshot, rule version, reasons, score and decision
- Per-account behavioural state built asynchronously from consumed events (velocity, destinations, failures, amount history)
- The fail-safe contract: uncertainty holds a payment for review, never allows or auto-rejects
- Independence of the sync decision path from the async state feed
- A transactional outbox publishing risk events to Pub/Sub with the ABS envelope
- PostgreSQL (local and production), Alembic migrations
- Google Cloud Run deployment
- Terraform for all infrastructure
- CI/CD (GitHub Actions): lint, test, Terraform validate, deploy
- Automated test suite
- OpenAPI documentation (/docs)

## Not in scope

- A machine-learning model (deliberately: deterministic rules are the engineering point)
- The orchestrator-side integration change to call the engine (a separate, sequenced step in the orchestrator repo)
- A human review UI or workflow (the orchestrator's RISK_REVIEW state and review endpoint own that)
- Real personal or financial data
- Authentication on the engine's own endpoints

## Definition of Done

- Live Cloud Run endpoint returning 200 on /health
- `POST /risk/evaluate` returning explainable decisions against real behavioural state
- The fail-safe and determinism properties shown under test, and the fail-safe shown against the live deployment
- Risk events published to Pub/Sub
- All tests green against PostgreSQL, CI/CD deploying on green main, Terraform validating
- /docs (Swagger) accessible on the live endpoint
- README complete with the decision model and live evidence
