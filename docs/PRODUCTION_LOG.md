# Production Log

Build and decision log for the risk engine. Newest milestone last.

## Milestone 1

### Goal
Freeze the design and build the deterministic decisioning core: the rules engine, the scoring bands, the state-freshness floor, and the decision contract.

### Completed
- The rules engine as a pure function of a feature snapshot and a configuration: ten weighted rules (high value, amount anomaly, velocity, short history, recent failures, new destination, destination reputation, beneficiary churn, structuring, and state-stale), a 0 to 100 score capped at 100, and a decision, plus a hash of the full configuration recorded with every result.
- The score-to-band mapping (ALLOW, REVIEW, BLOCK) with configurable boundaries, and the fail-safe floor that turns an ALLOW into a REVIEW when the behavioural state is stale.
- The evaluate request and response schemas, carrying the caller's evaluation_id for idempotency, the explainable reasons, the score, the rule version, the config hash, the state age and the echoed correlation_id.
- Config: the engine's own database (port 5434, so the ledger and orchestrator run alongside it locally), the event topic and the push subscription it will consume state from.
- Test count: 27 (the rules engine, the banding, the stale-state floor, and the config hash).

### Problems / Decisions
- The design was reviewed and refined before any code was frozen. Four changes prevented the nastiest later rewrites: an explicit state-freshness policy (a stale state floors the decision at review, so independence from the feed does not become deciding from garbage state), a caller-supplied evaluation_id so a timed-out retry returns the original decision, a feature snapshot (the derived, windowed values) rather than raw state so replay is exact, and a Pub/Sub push consumer rather than a background pull loop, which fits Cloud Run. Smaller corrections: persist observations and derive state from them, an immutable rule version pinned by a config hash, and the SHORT_HISTORY rule renamed to say what it means (observed history, not authoritative account age).
- Scoring is deterministic weighted rules, not a model. A decision is a pure function of the feature snapshot and the rule version, so it is replayable and explainable (ABS-REQ-014, 015). Weights and thresholds are configuration, so tuning risk bumps the rule version rather than editing the engine.
- The synchronous decision reads no clock and no live state beyond the snapshot, so it is independent of the asynchronous state feed (ABS-REQ-016), and the freshness floor keeps that independence from ever allowing a payment from state known to be stale (ABS-REQ-013).

### Evidence
- 27/27 tests: each rule fires on its own signal, the score is the sum of the fired weights capped at 100, the bands map exactly at their boundaries, a stale state never allows even with the rule's weight zeroed, the thresholds are configurable, the config hash is stable and change-sensitive, and the same snapshot and rule version replay to an identical decision.

### Next
Persistence: the append-only decision log (keyed for evaluation idempotency), the risk_observations evidence, the derived account_state, and Alembic migrations, with CI running the suite against the migrated schema. Then the evaluate service and API, and the Pub/Sub push consumer.

## Milestone 2

### Goal
Persistence: the append-only decision log, the observations and the derived account state, and migrations, with the test suite running against the migrated schema.

### Completed
- Data model: `risk_decisions` (unique `evaluation_id` for idempotency), `risk_observations` (`event_id` primary key for dedup, `occurred_at` for ordering by business time), `account_state` (derived cache with rolling windows for velocity, failures, destinations and amount average), and `outbox_events`.
- Alembic migration 001 creating all four tables, verified to upgrade and downgrade via the bare `alembic` console script.
- Database session and docker-compose (PostgreSQL on 5434).
- Test count: 27 to 32.

### Problems / Decisions
- The `decision` column is a plain string, not a Postgres enum. The orchestrator's one live failure was an enum name/value mismatch; a string carries the same three values with none of that risk.
- The test harness builds the schema by running `alembic upgrade head`, not `metadata.create_all`, so every model test is also a test that the ORM and the migration agree (ADR-014). This is the guard the orchestrator lacked, where the metadata-created and migration-created schemas were each self-consistent but disagreed with each other, and only production exercised the two together.

### Evidence
- 32/32 tests against the Alembic-migrated schema: decisions round-trip, a duplicate `evaluation_id` is rejected, a duplicate observation `event_id` is rejected at the database from a separate session, `account_state` defaults apply, and an outbox row starts unpublished. Migration 001 upgrades and downgrades cleanly via the bare `alembic` console script.

### Next
The evaluate service and API: build the feature snapshot from account_state, score it, persist the decision idempotently on `evaluation_id`, and emit the risk event through the outbox. Then the Pub/Sub push consumer that records observations and maintains the state.

## Milestone 3

### Goal
The evaluate service and the synchronous decision API.

### Completed
- The feature builder: turns persisted account state into the derived, windowed feature snapshot the engine scores (velocity, failures, beneficiary churn, structuring, observed age, destination novelty and reputation), and captures the snapshot for replay.
- The evaluate service: idempotent on `evaluation_id` (a retry returns the original decision and emits nothing new; a reused id with a different body is a conflict), persisting the decision and its `risk.evaluated` outbox event in one transaction, and flooring a decision at review when the event feed is stale.
- The destination watch list behind DESTINATION_REPUTATION, its version folded into the config hash.
- The API: `POST /risk/evaluate`, `GET /decisions/{evaluation_id}`, `GET /health`, with OpenAPI metadata to the same standard as the other services, and structured JSON logging.
- Test count: 32 to 47.

### Problems / Decisions
- State freshness is a property of the feed, not of an account. The stale signal is measured from the most recent observation across the whole feed, so a quiet account is not mistaken for a stopped consumer. This closes the gap the freshness policy exists for: independent of the feed, but not blind to it.
- The evaluate service is the only place that reads the clock and the stored state; it snapshots the derived features and hands a pure snapshot to the engine, so the decision stays replayable and the engine stays a pure function.

### Evidence
- 47/47 tests: a new account's small payment allows, high value reviews, an established clean payment allows, evaluation is idempotent and emits exactly one event, a reused id with a different body is a 409, a stale feed holds an otherwise clean payment, and every decision records its snapshot and config hash. API tests cover evaluate, retrieve, idempotency, conflict, validation and not-found, all against the migrated schema.

### Next
Events and the consumer: the outbox relay to Pub/Sub, and the `POST /events/pubsub` push endpoint that records observations and maintains account state. Then deploy (Dockerfile, CI, Terraform, Cloud Run) and integrate the orchestrator.

## Milestone 4

### Goal
Events and the consumer: the outbox relay to Pub/Sub, and the Pub/Sub push endpoint that records observations and maintains behavioural state.

### Completed
- Two transports (Pub/Sub, log) selected by `PUBSUB_TOPIC`, and the relay wrapping each outbox row in the full ABS envelope, publishing oldest first and stopping at the first failure so ordering holds and a failed row and everything after it stay pending.
- The Pub/Sub push consumer at `POST /events/pubsub`: it decodes the pushed message, validates the envelope, and records the observation and updates `account_state` in one transaction, deduplicated on `event_id` so redelivery is safe.
- Behavioural state maintenance: `payment.received` builds velocity, seen destinations, beneficiary changes and the amount average; `payment.failed` and `payment.rejected` build the failures signal; every event is recorded as an observation so state is rebuildable.
- Endpoints: `POST /events/pubsub`, `GET /outbox/pending`, `POST /outbox/publish`.
- Test count: 47 to 57.

### Problems / Decisions
- A push subscription, not a pull loop: the consumer is an HTTP endpoint, which fits Cloud Run's request-driven lifecycle and gives dedup and retry a natural home at the request boundary. A non-2xx makes Pub/Sub retry.
- The observation and the state update commit together, so state exists if and only if the observation was recorded, and a redelivered event is caught by the observation primary key before it can double-count.

### Evidence
- 57/57 tests: the envelope carries the full ABS contract with producer `risk-engine`; publish marks rows published and a failed publish leaves everything pending; a payment event builds state and a duplicate is a no-op; a failure event records a failure; and, end to end, five consumed payments build a velocity spike that a later evaluate fires. The push endpoint decodes, dedups, and rejects a malformed message.

### Next
Deploy: Dockerfile, start.sh, CI (lint, migrate, test against the migrated schema), and Terraform (the database and user on the shared instance, Artifact Registry, secrets, the risk topic and its push subscription, Cloud Run). Then wire the orchestrator to call `POST /risk/evaluate` with the fail-to-review contract.

## Milestone 5

### Goal
Make the engine deployable: the container, CI/CD, and the infrastructure as code.

### Completed
- Dockerfile and `start.sh` (migrate then serve), and a `.dockerignore`.
- CI: lint, then a migration applied to a clean database and the full suite run against the migrated schema on a PostgreSQL service container, then a deploy job to Cloud Run.
- Terraform for the engine's slice of the project: its database and user on the shared Cloud SQL instance, Artifact Registry, the database-url secret injected at runtime, a dedicated runner service account, the `risk-events` topic and its publisher grant, the Cloud Run service, and a push subscription on the orchestrator's `payment-events` topic delivering to the consumer endpoint, with a dead-letter topic and the Pub/Sub service-agent grants that dead-lettering needs. A `terraform` CI workflow (fmt, init, validate) matching the other services.

### Problems / Decisions
- The behavioural-state feed is a push subscription on the orchestrator's `payment-events` topic, not the engine's own topic: the engine consumes payment facts and publishes risk facts, so it subscribes to the orchestrator and publishes to `risk-events` for downstream services.
- A dead-letter topic bounds a poison message to five delivery attempts, so the unauthenticated push endpoint cannot be made to loop forever on a message it can never process.
- The service is public and the push endpoint unauthenticated, matching the orchestrator's posture for a portfolio. A production system would require an OIDC token on the push and authenticate the evaluate caller; this is a noted hardening, not a built one.

### Evidence
- `terraform fmt` clean and the provider resolves; the lock file carries the cross-platform hashes CI needs; 57/57 tests green against the migrated schema.

### Next
The live bootstrap and first deploy (the database and user, secrets, Artifact Registry, topics, and the push subscription once the service URL exists), then prove a live decision. Then wire the orchestrator to call `POST /risk/evaluate` with the fail-to-review contract.
