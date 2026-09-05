# Service Level Objectives

A risk decision is a single read of the account's behavioural state plus a pure scoring pass, with no cross-service calls on the request path. So unlike the orchestrator's payment path, which makes two synchronous ledger round trips, the engine's decision endpoint is a local operation and its latency floor is one database read.

## Defined SLOs

| Metric | Target | Measurement |
|--------|--------|-------------|
| Availability | 99.5% | Percentage of non-5xx responses during load test |
| Decision p50 | < 100ms | Median for `POST /risk/evaluate` |
| Decision p95 | < 200ms | 95th percentile for `POST /risk/evaluate` |
| Read p50 | < 100ms | Median for `GET /decisions/{id}` and `/health` |
| Error rate | < 1% | Percentage of 5xx responses |
| Throughput | > 5 req/s sustained | Aggregate under 5 concurrent users |
| Decision integrity | deterministic | The same input snapshot and rule version replay to the same decision |

## Load Test Configuration

- Tool: Locust ([`scripts/loadtest.py`](../scripts/loadtest.py))
- Target: `https://risk-engine-eppidgbmxa-nw.a.run.app`
- Users: 5 concurrent
- Duration: 60 seconds
- Workload mix: evaluate a payment (weight 5), retrieve the decision (2), health (1). Amounts and destinations are randomised so a spread of allow, review and block decisions is produced.

## Load Test Results

Run on 2026-09-05 against the live Cloud Run deployment. 810 requests over 60 seconds at 5 concurrent users, of which 525 were decisions.

| Metric | Target | Measured | Status |
|--------|--------|----------|--------|
| Availability | 99.5% | 100% (0 of 810 failed) | pass |
| Decision p50 | < 100ms | 63ms | pass |
| Decision p95 | < 200ms | 70ms | pass |
| Read p50 | < 100ms | 42ms (decisions 44ms, health 42ms) | pass |
| Error rate (5xx) | < 1% | 0% | pass |
| Throughput | > 5 req/s | 13.57 req/s | pass |

Per-endpoint medians: health 42ms, retrieve a decision 44ms, evaluate a payment 63ms. The decision p99 (110ms) and the single 498ms maximum are the first requests against a cold instance as Cloud Run scaled up from zero; steady-state evaluation sits at 63ms.

The engine is fast because a decision does no network I/O on the request path: it reads one account-state row and the feed's freshness, snapshots the features and scores them in memory. There is no bcrypt login, no ledger call and no provider call. The behavioural state that makes the decision meaningful is built entirely on the asynchronous path, off the request.

## Post-Load Verification

After the load test, `GET /health` returns 200 with the database connected, and the 525 decisions made during the run are persisted and retrievable by their evaluation id (the retrieve task returned 200 throughout the run). Because a decision is a pure function of its recorded snapshot and rule version, each remains replayable to the same result after the fact, which is the engine's integrity property rather than a balance to reconcile.
