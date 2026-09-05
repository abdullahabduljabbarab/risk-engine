# Engineering Report

## What this is

A payment risk engine. Given a payment it returns `ALLOW`, `REVIEW` or `BLOCK` with a score and the reasons behind it, and it is the decision the payment orchestrator waits on before it reserves any funds. It owns two things: the decision, made by a deterministic weighted rules engine, and the rolling behavioural state that informs it, built asynchronously from events. It never moves money, never contacts a provider, and never records a financial transaction. The system is built around three properties a risk service must have: an immediate decision, an explainable one, and one that is safe under its own failure.

## Architecture

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

FastAPI and Pydantic handle validation, routing and the OpenAPI spec. SQLAlchemy and Alembic own the schema and migrations. The engine keeps its own state (decisions, observations, account state, outbox) in its own database and never touches another service's tables. Deployed on GCP Cloud Run with auto-scaling, CI/CD via GitHub Actions using keyless Workload Identity Federation, and infrastructure defined in Terraform.

## Key engineering decisions

**A deterministic weighted rules engine, not a model.** The score is the sum of the weights of the rules a payment fires. This is explainable per decision, testable to an exact score, and free of the machine-learning tail of datasets, labels, drift and retraining. A decision reconstructable from its inputs is worth more here than a marginally more realistic opaque one.

**Feature snapshots for exact replay.** Several signals depend on time (velocity, failures, structuring windows, observed age). The engine scores a snapshot of the already-derived features, not raw state, and records it with the decision along with a hash of the full configuration. So `snapshot + rule version` replays to the same score, band and reasons long after the decision was made (ABS-REQ-014, 015).

**A state-freshness floor, so independence is not blindness.** The synchronous decision does not depend on the event feed being healthy, but it must not decide from state it knows is stale. Every snapshot carries the age of the feed; past a configured maximum the STATE_STALE rule fires and the decision is floored at review. A lost feed degrades to holding payments, never to allowing them from stale state (ABS-REQ-013, 016).

**Idempotent evaluation.** The orchestrator supplies a deterministic `evaluation_id`, unique in the decision table. A retry after a timeout returns the original decision and emits no second event, the same retry discipline the ledger and orchestrator apply to their own operations.

**Observations, then derived state.** Every consumed event is stored as an observation, and account state is a cache derived from them. State is rebuildable by replay, and out-of-order delivery is handled by ordering on `occurred_at` rather than assuming Pub/Sub arrival order is business order.

**A push consumer, not a pull loop.** Because the engine runs on Cloud Run, the event feed is a Pub/Sub push subscription delivering to an HTTP endpoint that validates, deduplicates and updates state transactionally, with a dead-letter queue bounding a poison message.

## Numbers

| Metric | Value |
|--------|-------|
| Test count | 58 |
| Alembic migrations | 1 (decisions, observations, account state, outbox) |
| API endpoints | 6 |
| Rules | 10 weighted signals, including the state-freshness floor |
| Decision bands | 3 (allow, review, block), configurable boundaries |
| ABS requirements owned | 4 (013, 014, 015, 016), plus event delivery (007, 008, 009) |

## Test categories

| Category | What they prove |
|----------|-----------------|
| Rules engine | Each rule fires on its signal, the score sums and caps at 100, the bands map exactly, thresholds are configurable, a stale state never allows, and the same snapshot replays identically |
| Banding | The score-to-band mapping is exact at its boundaries |
| Persistence | Decisions, observations, account state and outbox round-trip against the Alembic-migrated schema; evaluation_id and event_id are unique |
| Service | Idempotent evaluation, a conflict on a reused id, a decision from state, the stale-feed hold, and exactly one emitted event |
| Consumer | Observations recorded and deduplicated, state built from payment and failure events, malformed messages rejected |
| Relay | Full ABS envelope, at-least-once publish, order preserved, a failed publish leaves rows pending |
| API | Evaluate, retrieve, validation, not found, health |

## Injected and handled failures

- A redelivered event (at-least-once) is deduplicated on `event_id` and does not double-count state.
- A reused `evaluation_id` with a different request is a conflict, not a silent second decision.
- A stale event feed floors an otherwise-clean decision to review rather than allowing from stale state.
- A malformed pushed message is rejected, and the dead-letter policy bounds a poison message rather than looping on it.
- An unreachable engine, on the caller's side, holds the payment for review (proven in the orchestrator).

## Cloud architecture

| Component | Service | Region |
|-----------|---------|--------|
| API runtime | Cloud Run | europe-west2 (London) |
| Database | Cloud SQL PostgreSQL 16 (own schema and user on the shared instance) | europe-west2 |
| Container registry | Artifact Registry | europe-west2 |
| Event bus | Pub/Sub (own topic, push subscription on the orchestrator's topic, dead-letter) | europe-west2 |
| Secrets | Secret Manager | europe-west2 |
| CI/CD | GitHub Actions, keyless via Workload Identity Federation | Ubuntu runners |
| IaC | Terraform | All engine resources declared |

## V&V matrix

The full requirement-to-test mapping is in [VV_PLAN.md](VV_PLAN.md). The load test and its SLOs are in [SLO.md](SLO.md), the STRIDE threat model in [THREAT_MODEL.md](THREAT_MODEL.md), and the decisions and their trade-offs as ADRs in [DECISIONS.md](DECISIONS.md).

## Design trade-offs

**Rules over a model.** A weighted rules engine is deliberately chosen over machine learning; the contract and the explainability are the engineering point, and a model can be added later behind the same interface if it earns its keep.

**Snapshot-based replay over recomputation.** Storing the derived features with each decision costs a little space but makes replay exact and auditable, rather than depending on how a window would be recomputed later.

**A derived state cache over querying observations each time.** The synchronous evaluate reads a per-account cache rather than aggregating observations at request time, keeping the decision path fast; the cache is always rebuildable from the observations if it is ever lost.

**Authenticated push, public reads.** The consumer endpoint is protected because it mutates the state decisions read: the push subscription attaches a Google OIDC token minted for a dedicated service account, and the endpoint verifies it before applying an event. The read and decision endpoints stay public by scope decision, safe because the engine never moves money. Cloud Run IAM is per-service rather than per-path, so this mixed posture is enforced in the application, not the platform.
