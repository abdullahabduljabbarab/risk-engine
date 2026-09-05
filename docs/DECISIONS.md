# Architecture Decision Records

## ADR-001: A deterministic weighted rules engine, not a model

Scoring is a sum of configured rule weights, not a trained model. A rules engine is explainable per decision, testable to an exact score, and free of the machine-learning tail: datasets, labels, validation, drift, retraining and the burden of justifying all of them. For a system whose thesis is evidence and explicit contracts, a decision that can be reconstructed from its inputs is worth more than a marginally more "realistic" opaque one. A model can be added later behind the same contract if it ever earns its keep.

## ADR-002: A synchronous decision, an asynchronous state

The engine is reached two ways. The orchestrator calls `POST /risk/evaluate` and waits, and the engine also consumes payment and transaction events to build rolling behavioural state. Keeping these separate means the decision path has no dependency on a broker being healthy or a consumer being caught up. A payment is never blocked on the freshness of the behavioural feed; it is decided on the snapshot available at evaluate time (ABS-REQ-016). Forcing the decision through events too, to make the engine feel more distributed, would turn the orchestrator's synchronous payment path async for no real gain.

## ADR-003: Fail towards holding, never towards moving money

The orchestrator treats the engine as advisory-but-required. If the engine times out or is unreachable, the payment goes to RISK_REVIEW and is held. It is not allowed, because an unscored payment must not settle, and it is not auto-rejected, because a transient outage is not a decision to refuse a customer. This is the risk equivalent of the orchestrator's reservation rule: when the system is unsure, it fails towards holding (ABS-REQ-013).

## ADR-004: Configurable weights and thresholds, versioned

Rule weights, thresholds and band boundaries are configuration, not code. Tuning risk tolerance is changing a value and bumping the rule version. Because every decision records the rule version it was made under, changing the configuration never rewrites the meaning of past decisions; each stays replayable against the version in force when it was made (ABS-REQ-014).

## ADR-005: Snapshot-based replayability

A decision is a pure function of an input snapshot and the rule version. The snapshot captures the behavioural values used, so the decision reads no clock and no live state beyond what it recorded. This is what makes a decision reproducible long after it was made, and auditable: the same snapshot and version always yield the same score, band and reasons (ABS-REQ-014, 015).

## ADR-006: An append-only decision log

Decisions are never updated or deleted. The decision log is the audit trail of every judgement the engine has made, each carrying enough to explain and replay it. This matches the append-only discipline of the ledger and the orchestrator's event log: history is preserved, not overwritten.

## ADR-007: Its own database, on port 5434 locally

The engine keeps its own state (decisions, behavioural state, outbox) in its own database, reached only by the engine. Locally it runs on port 5434 so the ledger (5432) and orchestrator (5433) run alongside it without colliding. It never reads or writes another service's tables; state that belongs to the ledger or the orchestrator is reached through their APIs or their events, never their databases.

## ADR-008: A transactional outbox for risk events

Risk events are written to an outbox table in the same transaction as the decision, so an event exists if and only if the decision committed. The relay publishes at least once and consumers deduplicate on event_id, the same event discipline the ledger and orchestrator use, rather than a dual write to a broker that could succeed while the database rolls back.
