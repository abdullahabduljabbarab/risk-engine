# Production Log

Build and decision log for the risk engine. Newest milestone last.

## Milestone 1

### Goal
Scaffold the service and the deterministic decisioning core: the rules engine, the scoring bands, and the decision contract.

### Completed
- The rules engine as a pure function of an input snapshot and a configuration: nine weighted rules over the payment and the account's behavioural state (high value, amount anomaly, velocity, new account, recent failures, new destination, destination reputation, beneficiary churn, structuring), a 0 to 100 score capped at 100, and a decision.
- The score-to-band mapping (ALLOW, REVIEW, BLOCK) with configurable boundaries.
- The evaluate request and response schemas, carrying the explainable reasons, the score, the rule version and the echoed correlation_id.
- Config: the engine's own database (port 5434, so the ledger and orchestrator run alongside it locally), the event topic and the subscription it will consume state from.
- Test count: 23 (the rules engine and the banding).

### Problems / Decisions
- Scoring is deterministic weighted rules, not a model. A decision is a pure function of the input snapshot and the rule version, so it is replayable and explainable (ABS-REQ-014, 015). The weights and thresholds are configuration, so tuning risk bumps the rule version rather than editing the engine.
- The behavioural signals arrive already snapshotted on the input, so the engine reads no clock and no external state. The synchronous decision is therefore independent of the asynchronous state feed (ABS-REQ-016): decisions are computed from the snapshot, never blocked on how fresh the feed is.

### Evidence
- 23/23 tests: each rule fires on its own signal, the score is the sum of the fired weights capped at 100, the bands map exactly at their boundaries, the thresholds are configurable, and the same input and rule version replay to an identical decision.

### Next
Persistence: the append-only decision log, the account behavioural state, and migrations. Then the evaluate service and API with the fail-safe contract, and the synchronous decision and asynchronous state paths.
