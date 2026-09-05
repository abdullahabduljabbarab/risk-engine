# Architecture Decision Records

## ADR-001: A deterministic weighted rules engine, not a model

Scoring is a sum of configured rule weights, not a trained model. A rules engine is explainable per decision, testable to an exact score, and free of the machine-learning tail: datasets, labels, validation, drift, retraining and the burden of justifying all of them. For a system whose thesis is evidence and explicit contracts, a decision that can be reconstructed from its inputs is worth more than a marginally more "realistic" opaque one. A model can be added later behind the same contract if it ever earns its keep.

## ADR-002: A synchronous decision, an asynchronous state

The engine is reached two ways. The orchestrator calls `POST /risk/evaluate` and waits, and the engine also consumes payment and transaction events to build rolling behavioural state. Keeping these separate means the decision path has no dependency on a broker being healthy or a consumer being caught up. A payment is never blocked on the freshness of the behavioural feed; it is decided on the snapshot available at evaluate time (ABS-REQ-016). Forcing the decision through events too, to make the engine feel more distributed, would turn the orchestrator's synchronous payment path async for no real gain.

## ADR-003: Fail towards holding, never towards moving money

The orchestrator treats the engine as advisory-but-required. If the engine times out or is unreachable, the payment goes to RISK_REVIEW and is held. It is not allowed, because an unscored payment must not settle, and it is not auto-rejected, because a transient outage is not a decision to refuse a customer. The same rule applies when the engine is reachable but its behavioural state is stale: the STATE_STALE rule fires and the decision is floored at review, so a lost event feed cannot yield an allow from state the engine knows is out of date (ABS-REQ-013). Both are the risk equivalent of the orchestrator's reservation rule: when the system is unsure, it fails towards holding.

## ADR-004: Configurable weights and thresholds, versioned

Rule weights, thresholds and band boundaries are configuration, not code. Tuning risk tolerance is changing a value and bumping the rule version. Because every decision records the rule version it was made under, changing the configuration never rewrites the meaning of past decisions; each stays replayable against the version in force when it was made (ABS-REQ-014).

## ADR-005: Feature-snapshot replayability

A decision is a pure function of a feature snapshot and the rule version. Several signals depend on time (velocity, failures, structuring windows, observed age), so the snapshot captures the already-derived, already-windowed features that were scored, not raw state and never the clock. This is what makes a decision reproducible long after it was made, and auditable: the same feature snapshot and version always yield the same score, band and reasons (ABS-REQ-014, 015). Snapshotting raw state instead would leave replay depending on how those windows were recomputed later.

## ADR-006: An append-only decision log

Decisions are never updated or deleted. The decision log is the audit trail of every judgement the engine has made, each carrying enough to explain and replay it. This matches the append-only discipline of the ledger and the orchestrator's event log: history is preserved, not overwritten.

## ADR-007: Its own database, on port 5434 locally

The engine keeps its own state (decisions, behavioural state, outbox) in its own database, reached only by the engine. Locally it runs on port 5434 so the ledger (5432) and orchestrator (5433) run alongside it without colliding. It never reads or writes another service's tables; state that belongs to the ledger or the orchestrator is reached through their APIs or their events, never their databases.

## ADR-008: A transactional outbox for risk events

Risk events are written to an outbox table in the same transaction as the decision, so an event exists if and only if the decision committed. The relay publishes at least once and consumers deduplicate on event_id, the same event discipline the ledger and orchestrator use, rather than a dual write to a broker that could succeed while the database rolls back.

## ADR-009: A state-freshness floor, so independence is not blindness

The synchronous decision must not depend on the broker, but "independent of the feed" must not slide into "willing to decide from garbage state." Every snapshot carries the age of the behavioural state. When that age exceeds a configured maximum, the STATE_STALE rule fires and the decision is floored at review, whatever the numeric score. This closes the gap between ABS-REQ-016 (decisions are not blocked on the feed) and ABS-REQ-013 (uncertainty never moves money): a lost feed degrades to holding payments for review, never to allowing them from state known to be stale.

## ADR-010: Idempotent evaluation on a caller-supplied evaluation_id

The orchestrator can time out after the engine has committed a decision but before the response arrives. Without an idempotency key, its retry would make a second decision and emit a second event. So evaluation is keyed on an `evaluation_id` the orchestrator supplies, unique in the decision table. The same id returns the original decision; the same id with a different request body is a conflict. This is the same retry discipline the ledger applies to transactions and the orchestrator to reserve, capture and release.

## ADR-011: A Pub/Sub push consumer, not a background pull loop

The engine runs on Cloud Run, which is request-driven and scales to zero. A permanent streaming-pull loop inside the web container fits that lifecycle badly. Instead the subscription is a push subscription that delivers to `POST /events/pubsub`; the handler validates, deduplicates, updates state transactionally and returns 2xx, and a failure returns non-2xx so Pub/Sub retries. This also gives the retry and dedup behaviour a natural home at the HTTP boundary rather than inside a long-lived process.

## ADR-012: Persist observations, derive state from them

Behavioural state is a derived cache, not the only copy of the evidence. Every consumed event is stored as an observation with its `occurred_at`, and account state is computed from those observations. Two things follow: state is rebuildable by replaying observations, and out-of-order delivery is handled by ordering on `occurred_at` rather than assuming Pub/Sub arrival order is business order. Keeping only a derived state and the processed-id set, as a first cut did, would have thrown away the evidence and the ability to reason about ordering.

## ADR-013: Immutable rule versions with a configuration hash

A rule version is only useful if you can recover exactly what it represented. Each decision records a `rule_config_hash` over the full configuration: weights, thresholds, band boundaries and the watch-list version. Once a version has been used its configuration is immutable; a change creates a new version. This matters most for DESTINATION_REPUTATION, where the watch list can change: the reputation result is captured in the feature snapshot and the watch-list version is in the hash, so a decision replays against the list that was actually in force rather than today's.

## ADR-014: CI runs against the Alembic-migrated schema

Carried directly from the orchestrator, where a live failure came from the ORM-metadata schema and the Alembic-migrated schema being individually self-consistent but disagreeing with each other. From day one, CI applies `alembic upgrade head` against PostgreSQL and runs the integration suite against that migrated schema, so the two representations are exercised together rather than only apart.
