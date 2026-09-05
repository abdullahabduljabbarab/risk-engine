# Threat Model (STRIDE)

## Scope

This threat model covers the risk-engine service: a payment risk decisioning service deployed on GCP Cloud Run with Cloud SQL PostgreSQL, reached synchronously by the orchestrator and fed asynchronously by a Pub/Sub push subscription. It does not cover the ledger's or orchestrator's own threat models, network-level DDoS, or physical security of cloud infrastructure.

## Assets

| Asset | Sensitivity | Location |
|-------|-------------|----------|
| Decision log | High (audit trail, must be reconstructable) | Risk Cloud SQL |
| Behavioural state and observations | Medium (informs decisions) | Risk Cloud SQL |
| The integrity of a decision (right score, right reasons) | High | Enforced by the deterministic engine and the snapshot |
| Database credentials | Critical | Secret Manager |
| Deploy identity (WIF, deploy service account) | Critical | GCP IAM, no key stored |

## Threat Analysis

### S: Spoofing

| Threat | Mitigation | Test |
|--------|------------|------|
| Attacker forges payment events to skew an account's behavioural state | The state only informs a score and never moves money, and a malformed message is rejected. OIDC verification on the push endpoint is a noted gap. | `test_push_endpoint_rejects_malformed` |
| Attacker replays events to inflate velocity or history | Consumed events are deduplicated on `event_id`, so a redelivery does not double-count. | `test_duplicate_event_is_a_noop`, `test_observation_dedup_on_event_id` |

### T: Tampering

| Threat | Mitigation | Test |
|--------|------------|------|
| Modifying a decision after the fact | The decision log is append-only and records the feature snapshot and a hash of the configuration, so a decision is reconstructable and a changed input or config is detectable. | `test_decision_records_snapshot_and_config_hash` |
| Silently changing a rule version's meaning | A used rule version's configuration is immutable; the config hash recorded with each decision changes if any weight, threshold, band or watch-list version does. | `test_config_hash_is_stable_and_sensitive` |
| SQL injection via API parameters | SQLAlchemy ORM with parameterised queries; Pydantic validates and coerces input first. | `test_invalid_amount_rejected` |

### R: Repudiation

| Threat | Mitigation | Test |
|--------|------------|------|
| Denial that a decision was made or why | Every decision is persisted with its snapshot, rule version, reasons, score and correlation_id, and is replayable to the same result. | `test_same_snapshot_and_version_is_replayable`, `test_reasons_reconstruct_the_score` |
| A decision cannot be tied to a request | The decision echoes and stores the caller's correlation_id, and emits an event carrying it, so it traces across services. | `test_emits_a_risk_evaluated_event` |

### I: Information Disclosure

| Threat | Mitigation | Test |
|--------|------------|------|
| Enumeration of decisions | Decisions and accounts are keyed by UUID; a decision is fetched by the caller's own evaluation_id, not a sequential id. | `test_get_missing_decision_404` |
| Stack traces or internal state in responses | Structured JSON errors, no stack traces. | `test_get_missing_decision_404` |
| Credential leakage in logs | Structured logs record metadata, not request bodies or credentials. | Log format in `app/logging.py` |

### D: Denial of Service

| Threat | Mitigation | Test |
|--------|------------|------|
| A poison event retried forever against the push endpoint | A dead-letter policy bounds delivery attempts, so a message that can never be processed is parked, not looped. | Dead-letter policy in `terraform/` |
| Resource exhaustion via large outbox reads | The outbox endpoints are cursor-limited. Cloud Run scales with connection pooling. | Query limits on the outbox endpoints |
| Malicious evaluate payloads | Pydantic rejects a non-positive amount and invalid types before the database. | `test_invalid_amount_rejected` |

### E: Elevation of Privilege

| Threat | Mitigation | Test |
|--------|------------|------|
| Compromise of the engine to move money | The engine holds no ledger credential and has no path to financial state; its worst case is a wrong decision, which the orchestrator resolves towards holding, not moving. | Architecture boundary (ABS-REQ-001) |
| Compromise of the deploy identity | The deploy is keyless: a repository-scoped Workload Identity binding, not a stored key, so there is no long-lived credential to steal. | WIF binding in `terraform/` |

## Mitigations Not Yet Implemented

| Gap | Risk | Priority |
|-----|------|----------|
| OIDC verification on the push endpoint | Medium: a forged event could skew behavioural state, bounded because state never moves money | Would add in production |
| Authentication on the evaluate endpoint | Low to medium: any caller can request a decision, which has no financial effect | Would add in production |
| Authoritative account age via `account.created` | Low: SHORT_HISTORY measures observed history, not true account age | Would add with the ABS contract |
| Rate limiting | Low: API abuse | Would add via Cloud Armor or middleware |

## Requirement-to-Test Traceability

| Requirement | Tests |
|-------------|-------|
| Uncertainty never permits money to move | `test_stale_state_never_allows_even_when_otherwise_clean`, `test_stale_floor_holds_even_if_weight_is_zeroed`, `test_stale_feed_holds_an_otherwise_clean_payment` |
| Deterministic and replayable decisions | `test_same_snapshot_and_version_is_replayable`, `test_config_hash_is_stable_and_sensitive` |
| Explainable decisions | `test_reasons_reconstruct_the_score`, `test_decision_records_snapshot_and_config_hash` |
| Consumers tolerate duplicate delivery | `test_duplicate_event_is_a_noop`, `test_observation_dedup_on_event_id`, `test_push_endpoint_decodes_and_dedups` |
| Idempotent evaluation | `test_evaluation_is_idempotent`, `test_reused_id_with_different_request_conflicts` |
