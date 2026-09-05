# Risk Engine

[![CI](https://github.com/abdullahabduljabbarab/risk-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/abdullahabduljabbarab/risk-engine/actions/workflows/ci.yml)
[![Terraform](https://github.com/abdullahabduljabbarab/risk-engine/actions/workflows/terraform.yml/badge.svg?branch=main)](https://github.com/abdullahabduljabbarab/risk-engine/actions/workflows/terraform.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Deterministic payment risk decisioning. Given a payment, the engine returns `ALLOW`, `REVIEW` or `BLOCK` with a score and the reasons behind it, and it is the decision the payment orchestrator waits on before it reserves any funds, so a risky payment is held before money moves rather than after. Scoring is a deterministic weighted rules engine, not a model, so every decision is explainable and replayable. The behavioural state that makes a decision meaningful is built asynchronously from events, and the decision path is independent of it but never blind to its freshness. It is deployed on Google Cloud Run, its infrastructure is defined in Terraform, and it ships through a keyless CI pipeline that authenticates with Workload Identity Federation.

It is one service in [ABS Financial Systems](https://github.com/abdullahabduljabbarab/abs-financial-systems): the ledger owns money, the orchestrator owns the payment lifecycle, and the risk engine owns deterministic decisioning and the behavioural evidence behind it. Pub/Sub moves facts between them.

**Live service**

- Interactive API reference (Swagger UI): https://risk-engine-eppidgbmxa-nw.a.run.app/docs
- Health probe: https://risk-engine-eppidgbmxa-nw.a.run.app/health

![Swagger overview](docs/images/01-swagger-overview.png)

---

## Contents

- [What it does](#what-it-does)
- [The two paths](#the-two-paths)
- [The fail-safe property](#the-fail-safe-property)
- [The rules engine](#the-rules-engine)
- [Behavioural state](#behavioural-state)
- [Architecture](#architecture)
- [Verification and evidence](#verification-and-evidence)
- [Running it locally](#running-it-locally)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)

---

## What it does

The engine decides whether a payment may proceed and says why. A `POST /risk/evaluate` returns a decision, a 0 to 100 score, and the exact rules that produced it. Here a high-value payment from a never-seen account to a new destination scores 63 and is held for review, with the three rules that fired and their weights:

![A review decision](docs/images/02-evaluate-review.png)

Every decision records the input snapshot, the rule version and a hash of the configuration, so it is explainable and replayable, and it is fetchable afterwards by the caller's `evaluation_id`:

![Retrieve a decision](docs/images/04-retrieve-decision.png)

The call is idempotent on that `evaluation_id`. A retry with the same id returns the original decision; the same id with a different request body is refused, so a retry can never silently produce a second, different decision:

![Idempotency conflict](docs/images/03-idempotency-conflict.png)

---

## The two paths

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

**The decision path is synchronous.** The orchestrator calls and waits. This path reads the current behavioural state, snapshots the features and scores them, with no dependency on a broker being healthy or a consumer being caught up.

**The state path is asynchronous, over Pub/Sub push.** Payment events are pushed to `POST /events/pubsub`, where they are validated, deduplicated on `event_id`, and applied to state in one transaction. If the feed lags or stops, decisions still return; what they must not do is decide from state known to be stale, which the freshness floor below prevents.

---

## The fail-safe property

The property the ecosystem leans on: **uncertainty in the risk decision must never permit money to move.** It has two halves.

**When the engine is unreachable.** The orchestrator treats the engine as advisory-but-required. If the call times out or fails, it holds the payment in `RISK_REVIEW`, never allowing an unscored payment and never auto-rejecting a customer for an outage.

**When the engine is reachable but its state is stale.** Every snapshot carries the age of the event feed. Past a configured maximum, the `STATE_STALE` rule fires and the decision is floored at review, whatever the numeric score, so a lost feed degrades to holding payments, never to allowing them from stale state. Both halves are the same shape as the orchestrator's reservation rule: when the system is unsure, it fails towards holding.

---

## The rules engine

Scoring is a deterministic weighted rules engine. Each rule inspects a feature snapshot and fires or does not; a fired rule contributes its weight and a reason code. The score is the sum of the fired weights, capped at 100, and the decision is the band the score falls in.

| Rule | Fires when |
|------|-----------|
| HIGH_VALUE | The amount is over the high-value threshold |
| AMOUNT_ANOMALY | The amount is far above the account's average payment |
| VELOCITY_SPIKE | Too many payments from the account in the window |
| SHORT_HISTORY | The account has been observed for less than the threshold |
| RECENT_FAILURES | The account has several recent failed or blocked payments |
| NEW_DESTINATION | The destination has not been seen for this account before |
| DESTINATION_REPUTATION | The destination is on the watch list |
| BENEFICIARY_CHURN | The account has changed destinations repeatedly in the window |
| STRUCTURING | Repeated amounts just under a reporting threshold |
| STATE_STALE | The behavioural state is older than the maximum acceptable age |

```
score = min(100, sum of fired rule weights)

  0 - 39    ALLOW
 40 - 69    REVIEW
 70 - 100   BLOCK

then: if STATE_STALE fired and the band is ALLOW, floor to REVIEW.
```

Weights, thresholds and band boundaries are configuration, so tuning risk is changing a value and bumping the rule version, not editing the engine. The engine scores a **feature snapshot**: the already-derived, already-windowed values, not raw state and never the clock, so a decision replays exactly. Each decision records the snapshot, the rule version and a `rule_config_hash` over the full configuration, so the exact weights, thresholds, bands and watch-list version behind it are recoverable and immutable. The complete model is in [`docs/DESIGN.md`](docs/DESIGN.md).

---

## Behavioural state

Payment events are consumed to build rolling per-account state (velocity, destinations seen, recent failures, amount average, observed age). Every event is recorded as an observation, deduplicated on `event_id`, and account state is a cache derived from those observations, rebuildable by replay. The consumer accepts a pushed event and updates state transactionally:

![The consumer accepting an event](docs/images/06-events-consumer.png)

Live, Pub/Sub pushes real orchestrator payment events to the consumer, each processed and acknowledged:

![Pub/Sub deliveries in the logs](docs/images/08b-events-pubsub-logs.png)

This state is what turns a bare payment into a judged one. State is never truth; it only informs the score.

---

## Architecture

```mermaid
flowchart TD
    O[Payment Orchestrator] -->|POST /risk/evaluate| A[FastAPI: evaluate, decisions, events]
    A --> S[Service: feature snapshot, rules engine, decision log]
    S --> DB[(PostgreSQL 16 / Cloud SQL)]
    DB -->|same transaction| OB[outbox_events]
    OB --> R[Relay: POST /outbox/publish]
    R --> RT[Pub/Sub topic risk-events]

    PE[Pub/Sub payment-events] -->|push subscription| E[POST /events/pubsub]
    E --> DB

    subgraph GCP [Google Cloud, europe-west2]
        A
        S
        DB
        OB
        R
        RT
        E
    end
```

FastAPI and Pydantic handle validation, routing and the OpenAPI spec. SQLAlchemy and Alembic own the schema and migrations. The engine keeps its own state (`risk_decisions`, `risk_observations`, `account_state`, `outbox_events`) in its own `risk` database and user on the ledger's shared Cloud SQL instance, and never touches another service's tables.

![Cloud Run service](docs/images/07-cloud-run.png)

The full GCP stack is defined as code in [`terraform/`](terraform/): the database and user, Artifact Registry, the injected secret, a runner service account, the `risk-events` topic, the Cloud Run service, and a push subscription on the orchestrator's `payment-events` topic with a dead-letter queue.

| Its own database | Its own user | Secret injected, not plaintext |
|---|---|---|
| ![Cloud SQL databases](docs/images/10-cloudsql-databases.png) | ![Cloud SQL users](docs/images/10b-cloudsql-users.png) | ![Secret Manager](docs/images/11-secret-manager.png) |

The push subscription feeds the engine, with a dead-letter topic bounding a poison message. Because this endpoint changes the behavioural state that decisions read, it is authenticated: Pub/Sub attaches a Google OIDC token minted for a dedicated push service account, and the consumer verifies it before applying any event, so only authenticated deliveries can feed state. The read and decision endpoints stay public by scope decision.

![Push subscription](docs/images/09b-push-subscription.png)

**Deployment is keyless.** GitHub Actions lints, runs the full suite against a PostgreSQL service container built from the Alembic migrations, and validates the Terraform. On green it builds the image, pushes to Artifact Registry and deploys to Cloud Run, authenticating through **Workload Identity Federation**: GitHub presents a short-lived OIDC token that GCP exchanges to impersonate a repository-scoped deploy service account, so no long-lived key is stored in the repository.

| Keyless identity (WIF) | Least-privilege deploy account | Image registry |
|---|---|---|
| ![Workload Identity Federation](docs/images/13-workload-identity.png) | ![Deploy service account](docs/images/14-deploy-service-account.png) | ![Artifact Registry](docs/images/12-artifact-registry.png) |

---

## Verification and evidence

58 automated tests cover the rules engine, the banding, the persistence, the evaluate service, the push consumer and its authentication, the outbox relay, and the API. The suite builds its schema from the Alembic migrations, so the ORM and the migrations are exercised together, not only apart. On top of the unit tests the engine was proven against the live deployment and the rest of the ecosystem.

**Load test (Locust, live Cloud Run, 5 concurrent users, 60 seconds):**

| Metric | Target | Measured |
|--------|--------|----------|
| Requests | | 810 |
| Failures | 0 | 0 |
| Decision p50 | < 100ms | 63ms |
| Decision p95 | < 200ms | 70ms |
| Read p50 | < 100ms | 42ms |
| Throughput | > 5 req/s | 13.57 req/s |

A decision does no network I/O on the request path: it reads one account-state row and scores in memory. The engine deciding live, at 29 to 33ms per call:

![Decision logs](docs/images/08-cloud-run-logs.png)

**The full ecosystem loop, live.** The orchestrator's payment decision comes from the engine. A live 6000 payment was held by the engine at score 63 and left in RISK_REVIEW with no reservation and no money moved, and the engine's persisted decision carried the same `correlation_id` as the orchestrator's payment, so one request traces across both services:

```
orchestrator payment  ->  state: risk_review   reserve_tx: None
risk engine decision  ->  review  score 63  [HIGH_VALUE, SHORT_HISTORY, NEW_DESTINATION]
correlation_id matches across the orchestrator and the engine: True
```

**Requirement to evidence:**

| Requirement | How it is verified |
|-------------|--------------------|
| Uncertainty never permits money to move (ABS-REQ-013) | `test_stale_state_never_allows_even_when_otherwise_clean`, `test_stale_floor_holds_even_if_weight_is_zeroed`; live fail-to-review |
| Deterministic, replayable decisions (ABS-REQ-014) | `test_same_snapshot_and_version_is_replayable`, `test_config_hash_is_stable_and_sensitive` |
| Every decision is explainable (ABS-REQ-015) | `test_reasons_reconstruct_the_score`, `test_decision_records_snapshot_and_config_hash` |
| Sync decision independent of the async feed (ABS-REQ-016) | `test_new_account_small_payment_is_allowed`, `test_stale_feed_holds_an_otherwise_clean_payment` |
| Consumers tolerate duplicate delivery (ABS-REQ-008) | `test_duplicate_event_is_a_noop`, `test_push_endpoint_decodes_and_dedups` |
| Idempotent evaluation | `test_evaluation_is_idempotent`, `test_reused_id_with_different_request_conflicts` |
| The ORM and the migration agree | the persistence tests run against the Alembic-migrated schema |

The full mapping is in [`docs/VV_PLAN.md`](docs/VV_PLAN.md), the load-test write-up in [`docs/SLO.md`](docs/SLO.md), the engineering narrative in [`docs/ENGINEERING_REPORT.md`](docs/ENGINEERING_REPORT.md), and the STRIDE threat model in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

---

## Running it locally

Requires Docker and Python 3.12.

```bash
# 1. start PostgreSQL
docker compose up -d

# 2. install dependencies
pip install -r requirements.txt

# 3. point the app at the database and apply migrations
export DATABASE_URL=postgresql://risk:risk@localhost:5434/risk
alembic upgrade head

# 4. run the service
uvicorn app.main:app --reload
```

Then open http://localhost:8000/docs for Swagger. A decision needs no other service: `POST /risk/evaluate` scores from whatever behavioural state has been built. With no `PUBSUB_TOPIC` set, the outbox relay publishes to the structured log instead of Pub/Sub, so the full path runs without cloud credentials.

**Running the tests** (the suite creates its own test database and migrates it):

```bash
export TEST_DATABASE_URL=postgresql://risk:risk@localhost:5434/risk_test
pytest
```

---

## Design decisions

**A deterministic weighted rules engine, not a model.** Explainable per decision, testable to an exact score, and free of the machine-learning tail of datasets, labels and drift. A decision reconstructable from its inputs is worth more here than a marginally more realistic opaque one.

**A state-freshness floor.** Independence from the event feed must not become deciding from garbage state, so a stale feed floors a decision at review rather than allowing from state known to be out of date.

**Feature snapshots.** The engine scores the derived, windowed features and stores them with the decision, so replay is exact rather than depending on how a window would be recomputed later.

**Keyless deploy.** Workload Identity Federation removes the long-lived service-account key from CI entirely; GitHub proves its identity with a short-lived token, scoped to this repository.

More decisions and their trade-offs are in [`docs/DECISIONS.md`](docs/DECISIONS.md) and [`docs/ENGINEERING_REPORT.md`](docs/ENGINEERING_REPORT.md).

---

## Project layout

```
app/            FastAPI application, rules engine, feature builder, service, consumer, relay
migrations/     Alembic migration (decisions, observations, account state, outbox)
terraform/      Risk engine infrastructure as code, including the keyless WIF deploy identity
scripts/        Load test harness
tests/          58 tests: rules, banding, persistence, service, consumer, relay, API
docs/           Design, requirements, decisions, engineering report, threat model, security, SLOs, V&V, build log, evidence
```

---

Built by Abdullah Ameed Abduljabbar.
