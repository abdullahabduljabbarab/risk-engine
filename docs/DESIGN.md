# Risk Engine: Design

## Purpose

The risk engine decides whether a payment may proceed. Given a payment, it returns one of three decisions, `ALLOW`, `REVIEW` or `BLOCK`, with a score and the reasons behind it. It is the service the payment orchestrator consults before it reserves any funds, so a risky payment is stopped or held before money moves rather than after.

It owns two things and nothing else: the decision, and the behavioural state that informs it. It never moves money, never contacts a provider, and never records a financial transaction. The ledger is the authority on money and the orchestrator owns the payment lifecycle; the risk engine owns deterministic decisioning and the rolling account state that makes those decisions meaningful.

The whole reason it is a separate service is that a decision must be immediate, explainable and safe under its own failure. Immediate, because a payment waits on it. Explainable, because "blocked" with no reason is useless to an operator and unauditable to a regulator. Safe under failure, because the one thing a risk service must never do is let money move when it is uncertain.

## The two paths: a synchronous decision, an asynchronous state

The engine is reached two ways, and keeping them separate is the core of the design.

```
                 synchronous decision path
  orchestrator  ----  POST /risk/evaluate  ---->  risk engine
                <----  ALLOW | REVIEW | BLOCK ----

                 asynchronous state path
  Pub/Sub  ----  payment / transaction events  ---->  risk engine
                 (velocity, history, failures, destinations)
```

**The decision path is synchronous.** The orchestrator calls `POST /risk/evaluate` and waits for a decision. This path reads the current behavioural state and the payment in front of it, scores it, and returns. It has no dependency on a message broker being healthy or a consumer being caught up.

**The state path is asynchronous.** The engine consumes payment and transaction events from Pub/Sub to maintain rolling per-account state: how many payments recently, to which destinations, how many recent failures, the account's typical amounts. This state is what turns a bare payment into a judged one. It is deduplicated on `event_id` like every ABS consumer.

The separation is deliberate (ABS-REQ-016). If event consumption lags or stops, decisions still return, just against slightly staler state. A decision is never blocked on the freshness of the behavioural feed. The synchronous path degrades to "decide on what we know," never to "cannot decide."

## The decision contract

`POST /risk/evaluate`

```json
{
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
  "decision": "review",
  "band": "REVIEW",
  "score": 68,
  "rule_version": "2026.09.1",
  "reasons": [
    { "rule": "HIGH_VALUE", "weight": 25 },
    { "rule": "VELOCITY_SPIKE", "weight": 25 },
    { "rule": "NEW_DESTINATION", "weight": 18 }
  ],
  "correlation_id": "bd7e...",
  "evaluated_at": "2026-09-05T20:00:00Z"
}
```

The `correlation_id` is echoed so a single request is traceable across the orchestrator, the risk decision and the ledger transaction it leads to (ABS-REQ-009). For a reachable request the engine always returns a decision; it never returns "unknown". Uncertainty is the caller's concern, handled by the fail-safe below.

## The fail-safe property

The one property the whole ecosystem leans on here: **uncertainty in the risk decision must never permit money to move** (ABS-REQ-013).

The orchestrator treats the risk engine as advisory-but-required. If `POST /risk/evaluate` times out or is unreachable, the orchestrator does not guess. It moves the payment to `RISK_REVIEW` and holds it. It does not fail open (ALLOW) because that would let an unscored payment settle, and it does not auto-reject (BLOCK) because a transient outage is not a decision to refuse a customer. Held for a human or a retry is the only safe default.

This mirrors the orchestrator's own reservation rule. There, a payment is never sent to a provider before funds are reserved. Here, a payment is never allowed to move before a decision is made. Both are the same shape of invariant: when the system is unsure, it fails towards holding, never towards moving money.

## The rules engine

Scoring is a deterministic weighted rules engine, not a model. Each rule inspects the payment and the account's behavioural state, and either fires or does not. A fired rule contributes a weight and a reason code. The score is the sum of the fired weights, capped at 100.

### Signals and rules

| Rule | Fires when | Reads |
|------|-----------|-------|
| HIGH_VALUE | The amount is over the high-value threshold | Payment |
| AMOUNT_ANOMALY | The amount is far above the account's typical payment | Payment + state |
| VELOCITY_SPIKE | Too many payments from the account in a short window | State |
| NEW_ACCOUNT | The account is younger than the new-account threshold | State |
| RECENT_FAILURES | The account has several recent failed or blocked payments | State |
| NEW_DESTINATION | The destination has not been seen for this account before | State |
| DESTINATION_REPUTATION | The destination is on a watch list | Payment |
| BENEFICIARY_CHURN | The account has changed destinations repeatedly in a short window | State |
| STRUCTURING | Repeated amounts just under a reporting threshold | State |

The weights and thresholds are configuration, not code. Tuning risk is changing a config value and bumping the rule version, not editing the engine.

### Scoring and bands

```
score = min(100, sum of fired rule weights)

  0 - 39    ALLOW
 40 - 69    REVIEW
 70 - 100   BLOCK
```

The band boundaries are configurable. A payment that fires no rules scores 0 and is allowed; one that fires several high-weight rules crosses into review or block. The decision is always the band the score falls in, with the exact rules that produced it attached.

### Explainability and replayability

Every decision records the input snapshot it was made from, the rule version in force, each fired rule with its weight, the total score, and the band (ABS-REQ-015). Two consequences follow. An operator can see exactly why a payment was held. And the decision is replayable: the same input snapshot against the same rule version always produces the same score and decision (ABS-REQ-014), because the engine is a pure function of the snapshot and the config. Nothing about a decision depends on wall-clock time or on how fresh the event feed happened to be, beyond the snapshot that was captured when the decision was made.

## Behavioural state

The asynchronous path maintains one rolling record per account, updated from the event stream:

- recent payment timestamps, for velocity
- destinations seen, for novelty and beneficiary churn
- recent failed or blocked outcomes, for the failures signal
- a running summary of amounts, for the anomaly signal
- the account's first-seen time, for the new-account signal

The synchronous evaluate reads this record, snapshots the parts it uses into the decision, and scores. The record is state, not truth: it is derived from events and can be rebuilt by replaying them. It is never the authority on anything; it only informs the score.

## Data model

- **risk_decisions**: append-only. `id`, `payment_id`, `account_id`, `amount`, `destination`, `input_snapshot` (the state values used), `rule_version`, `score`, `band`, `decision`, `reasons`, `correlation_id`, `created_at`. This is the audit trail: every decision the engine ever made, reconstructable.
- **account_state**: one row per account. Rolling behavioural state as above, updated by the consumer. Rebuildable from events.
- **processed_events**: `event_id` primary key, for consumer deduplication (ABS-REQ-008).
- **outbox_events**: the transactional outbox for the risk events the engine emits, written in the same transaction as the decision.

## Events emitted

The engine emits a fact for each decision through a transactional outbox, so downstream services (analytics, notification) see risk activity without calling the engine. `risk.evaluated` carries the decision, score, band and reasons. Every event uses the ABS envelope with a unique `event_id` (ABS-REQ-007) and the payment's `correlation_id`.

## Requirements satisfied

ABS-REQ-013, 014, 015 and 016 are owned here. ABS-REQ-007, 008 and 009 apply to its events and its consumer. ABS-REQ-001 holds by construction: the engine never writes financial state. Each is mapped to a named test as the code is built.

## What this is not

It is not a machine-learning system. There is no model, no training data, no drift, and no per-decision opacity to explain away; a portfolio that reached for ML here would spend its effort defending datasets and labels instead of building the system. It is not a fraud oracle or a labelling service. It is not the authority on money, which is the ledger, nor on the payment lifecycle, which is the orchestrator. And it does not block a decision on the freshness of its behavioural state; the decision path and the state path are independent by design.
