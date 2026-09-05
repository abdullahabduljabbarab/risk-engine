# Risk Engine: Design

## Purpose

The risk engine decides whether a payment may proceed. Given a payment, it returns one of three decisions, `ALLOW`, `REVIEW` or `BLOCK`, with a score and the reasons behind it. It is the service the payment orchestrator consults before it reserves any funds, so a risky payment is stopped or held before money moves rather than after.

It owns two things and nothing else: the decision, and the behavioural evidence that informs it. It never moves money, never contacts a provider, and never records a financial transaction. The ledger is the authority on money and the orchestrator owns the payment lifecycle; the risk engine owns deterministic decisioning and the rolling account evidence that makes those decisions meaningful.

The whole reason it is a separate service is that a decision must be immediate, explainable and safe under its own failure. Immediate, because a payment waits on it. Explainable, because "blocked" with no reason is useless to an operator and unauditable to a regulator. Safe under failure, because the one thing a risk service must never do is let money move when it is uncertain.

## The two paths: a synchronous decision, an asynchronous state

The engine is reached two ways, and keeping them separate is the core of the design.

```
                   FAST / SYNCHRONOUS
  orchestrator ─── POST /risk/evaluate ──▶ risk engine
               ◀── ALLOW | REVIEW | BLOCK ─   │
                                              │ reads
                                              ▼
                                     behavioural state
                                              ▲
                                              │ updates (transactional)
  Pub/Sub push ─── POST /events/pubsub ───────┘
                   ASYNCHRONOUS
```

**The decision path is synchronous.** The orchestrator calls `POST /risk/evaluate` and waits. This path reads the current behavioural state, builds a feature snapshot, scores it, and returns. It has no dependency on a message broker being healthy or a consumer being caught up.

**The state path is asynchronous, and driven by Pub/Sub push.** Because the engine runs on Cloud Run, it does not hold a permanent background pull loop inside the web container. Instead the `payment-events` (and later transaction) subscription is a push subscription that delivers to `POST /events/pubsub`. The handler validates the ABS envelope, deduplicates on `event_id`, records the observation and updates the derived state in one transaction, and returns 2xx. A processing failure returns non-2xx and Pub/Sub retries, so delivery is at-least-once and the handler is idempotent (ABS-REQ-008).

The separation is deliberate (ABS-REQ-016). If event delivery lags or stops, decisions still return. What they must not do is silently allow a payment from state the engine knows is stale, which is the freshness policy below.

## The decision contract

`POST /risk/evaluate`

```json
{
  "evaluation_id": "e1e2...",
  "payment_id": "0f4c...",
  "account_id": "9efc...",
  "amount": "250.00",
  "destination": "acme",
  "correlation_id": "bd7e..."
}
```

```json
{
  "decision_id": "a1b2...",
  "evaluation_id": "e1e2...",
  "decision": "review",
  "band": "REVIEW",
  "score": 68,
  "rule_version": "2026.09.1",
  "rule_config_hash": "9f86d0...",
  "reasons": [
    { "rule": "HIGH_VALUE", "weight": 25 },
    { "rule": "VELOCITY_SPIKE", "weight": 25 },
    { "rule": "NEW_DESTINATION", "weight": 18 }
  ],
  "state_age_seconds": 12.4,
  "correlation_id": "bd7e...",
  "evaluated_at": "2026-09-05T20:00:00Z"
}
```

The `correlation_id` is echoed so a single request is traceable across the orchestrator, the risk decision and the ledger transaction it leads to (ABS-REQ-009). For a reachable request the engine always returns a decision; it never returns "unknown". Uncertainty is the caller's concern, handled by the fail-safe below.

**The call is idempotent.** The orchestrator supplies `evaluation_id`, which is unique. The orchestrator can time out after the engine committed a decision but before the response arrived; a retry with the same `evaluation_id` returns the original decision and does not make a second one or emit a second event. The same `evaluation_id` with a different request body is a conflict. This is the same retry discipline the ledger and orchestrator use on their operations.

## The fail-safe property

The one property the whole ecosystem leans on here: **uncertainty in the risk decision must never permit money to move** (ABS-REQ-013). It has two halves.

**When the engine is unreachable.** The orchestrator treats the engine as advisory-but-required. If `POST /risk/evaluate` times out or is unreachable, the orchestrator does not guess. It moves the payment to `RISK_REVIEW` and holds it. It does not fail open (ALLOW), because that would let an unscored payment settle, and it does not auto-reject (BLOCK), because a transient outage is not a decision to refuse a customer.

**When the engine is reachable but its state is stale.** Independence from the broker must not become "knowingly decide from garbage state." Every snapshot carries `state_age_seconds`. If the behavioural state is older than the configured maximum acceptable age, the `STATE_STALE` rule fires and the decision is floored at REVIEW, whatever the numeric score. So an engine that has lost its event feed for hours cannot score a payment at 12 and return ALLOW from state it knows is out of date; it returns REVIEW and the payment is held. Both halves are the same shape as the orchestrator's reservation rule: when the system is unsure, it fails towards holding, never towards moving money.

## The rules engine

Scoring is a deterministic weighted rules engine, not a model. Each rule inspects a feature snapshot and either fires or does not. A fired rule contributes a weight and a reason code. The score is the sum of the fired weights, capped at 100.

### Signals and rules

| Rule | Fires when | Reads |
|------|-----------|-------|
| HIGH_VALUE | The amount is over the high-value threshold | Amount |
| AMOUNT_ANOMALY | The amount is far above the account's average payment | Amount + state |
| VELOCITY_SPIKE | Too many payments from the account in the window | State |
| SHORT_HISTORY | The account has been observed for less than the threshold | State |
| RECENT_FAILURES | The account has several recent failed or blocked payments | State |
| NEW_DESTINATION | The destination has not been seen for this account before | State |
| DESTINATION_REPUTATION | The destination is on the watch list | Payment + watch list |
| BENEFICIARY_CHURN | The account has changed destinations repeatedly in the window | State |
| STRUCTURING | Repeated amounts just under a reporting threshold | State |
| STATE_STALE | The behavioural state is older than the maximum acceptable age | State freshness |

`SHORT_HISTORY` is named honestly. The engine only knows when it first observed an account, not when the account was created, so it measures observed history, not authoritative account age. A true account-age signal needs ABS to emit an authoritative `account.created` fact; until then this rule means "short observed history", not "new account".

The weights and thresholds are configuration, not code. Tuning risk is changing a config value and bumping the rule version, not editing the engine.

### Scoring and bands

```
score = min(100, sum of fired rule weights)

  0 - 39    ALLOW
 40 - 69    REVIEW
 70 - 100   BLOCK

then: if STATE_STALE fired and the band is ALLOW, floor to REVIEW.
```

The band boundaries are configurable. A payment that fires no rule scores 0 and is allowed; one that fires several crosses into review or block. The decision is the band the score falls in, with the exact rules that produced it attached, subject to the stale-state floor.

### Explainability and replayability

The engine scores a **feature snapshot**: the already-derived, already-windowed values a decision was made from, for example:

```json
{
  "amount": "250.00",
  "payment_count_window": 4,
  "failed_count_window": 2,
  "average_amount": "81.42",
  "destination_seen_before": false,
  "destination_on_watchlist": false,
  "beneficiary_count_window": 5,
  "structuring_count_window": 0,
  "observed_age_seconds": 483920,
  "state_age_seconds": 12,
  "state_as_of": "2026-09-05T19:59:47Z",
  "evaluation_time": "2026-09-05T20:00:00Z"
}
```

Several signals depend on time (velocity, failures, structuring windows, observed age). Snapshotting the derived features rather than raw state is what makes replay exact: the engine reads no clock and no live state beyond the snapshot, so `FeatureSnapshot + rule version` always yields the same score, band and reasons (ABS-REQ-014). Every decision records the snapshot, the rule version, and a `rule_config_hash` over the full configuration including the watch-list version, so the exact weights, thresholds and bands behind it are recoverable and cannot silently mutate (ABS-REQ-015). Once a rule version has been used its configuration is immutable; a change creates a new version. For `DESTINATION_REPUTATION` this matters most: the reputation result is captured as a boolean in the snapshot and the watch-list version is in the config hash, so a decision replays against the list that was actually in force.

## Behavioural state and observations

The asynchronous path keeps two things. It records every observation it consumes, and it maintains a derived state per account for fast reads.

- **Observations** are the evidence: each consumed event stored with its `event_id`, `event_type`, `occurred_at`, `account_id`, `payment_id`, `correlation_id` and payload. This is the source of truth for behavioural state, so state can be rebuilt by replaying observations, and out-of-order delivery is handled by `occurred_at` rather than by assuming Pub/Sub arrival order is business order.
- **Account state** is a derived cache computed from the observations: recent payment timestamps for velocity, destinations seen for novelty and churn, recent failed or blocked outcomes, an averaged amount for the anomaly signal, and the first-observed time. The synchronous evaluate reads this cache, snapshots the features it uses, and scores.

The state is derived, never truth: it can be rebuilt from observations and never authoritatively holds anything. It only informs the score.

## Data model

- **risk_decisions**: append-only. `id`, `evaluation_id` (unique), `payment_id`, `account_id`, `amount`, `destination`, `feature_snapshot`, `rule_version`, `rule_config_hash`, `score`, `band`, `decision`, `reasons`, `correlation_id`, `created_at`. The audit trail: every decision, replayable.
- **risk_observations**: one row per consumed event. `event_id` primary key (dedup, ABS-REQ-008), `event_type`, `occurred_at`, `account_id`, `payment_id`, `correlation_id`, `payload`. The evidence behavioural state is derived from.
- **account_state**: one row per account. The derived cache described above, rebuildable from observations.
- **outbox_events**: the transactional outbox for the risk events the engine emits, written in the same transaction as the decision.

## Events emitted

The engine emits a fact for each decision through a transactional outbox, so downstream services (analytics, notification) see risk activity without calling the engine. `risk.evaluated` carries the decision, score, band and reasons. Every event uses the ABS envelope with a unique `event_id` (ABS-REQ-007) and the payment's `correlation_id`.

## Requirements satisfied

ABS-REQ-013, 014, 015 and 016 are owned here. ABS-REQ-007, 008 and 009 apply to its events and its consumer. ABS-REQ-001 holds by construction: the engine never writes financial state. Each is mapped to a named test as the code is built.

## What this is not

It is not a machine-learning system. There is no model, no training data, no drift, and no per-decision opacity to explain away; a portfolio that reached for ML here would spend its effort defending datasets and labels instead of building the system. It is not a fraud oracle or a labelling service. It is not the authority on money, which is the ledger, nor on the payment lifecycle, which is the orchestrator. It does not resolve held payments; review resolution is owned outside this service and is not part of this MVP. And it does not decide from state it knows is stale: the decision path is independent of the state feed, but not blind to its freshness.
