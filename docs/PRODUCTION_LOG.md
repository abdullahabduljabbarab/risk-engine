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
