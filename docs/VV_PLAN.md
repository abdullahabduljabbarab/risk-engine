# Verification and Validation Plan

## Approach

Every requirement the risk engine owns is verified by an automated test, and every behaviour that can be driven deterministically is additionally proven against the live Cloud Run deployment. CI runs the full suite (57 tests) against a PostgreSQL service container on every push, and the suite builds its schema by running the Alembic migrations, so the ORM and the migrations are exercised together rather than only apart (ADR-014). The requirements are the ABS system requirements owned by the engine, defined in [SYSTEM_REQUIREMENTS.md](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md).

## Requirement-to-Test Mapping

| Requirement | Verification | Test / Evidence |
|-------------|-------------|-----------------|
| ABS-REQ-013 (uncertainty never permits money to move) | Automated + Live | `test_stale_state_never_allows_even_when_otherwise_clean`, `test_stale_floor_holds_even_if_weight_is_zeroed`, `test_stale_feed_holds_an_otherwise_clean_payment`; live, the orchestrator holds a reviewed payment with no reservation |
| ABS-REQ-014 (deterministic, replayable decisions) | Automated | `test_same_snapshot_and_version_is_replayable`, `test_config_hash_is_stable_and_sensitive` |
| ABS-REQ-015 (every decision is explainable) | Automated | `test_reasons_reconstruct_the_score`, `test_decision_records_snapshot_and_config_hash` |
| ABS-REQ-016 (sync decision independent of the async feed) | Automated | `test_new_account_small_payment_is_allowed` (a decision returns with no state), `test_stale_feed_holds_an_otherwise_clean_payment` (staleness is handled by the floor, not by blocking) |
| ABS-REQ-007 (globally unique event_id) | Automated | `test_event_id_is_stable_for_consumer_dedup`, outbox ids are UUIDs |
| ABS-REQ-008 (consumers tolerate duplicate delivery) | Automated | `test_duplicate_event_is_a_noop`, `test_observation_dedup_on_event_id`, `test_push_endpoint_decodes_and_dedups` |
| ABS-REQ-009 (one correlation_id across services) | Automated + Live | `test_emits_a_risk_evaluated_event` (event carries the decision's correlation_id); live, the engine's decision carries the orchestrator payment's correlation_id |
| ABS-REQ-001 (engine never writes financial state) | By design | No ledger access anywhere in the service; it only reads and writes its own database |

## Behavioural Coverage

| Behaviour | Verification | Test / Evidence |
|-----------|-------------|-----------------|
| Each rule fires on its own signal | Automated | `test_high_value_fires`, `test_amount_anomaly_fires_against_account_average`, `test_velocity_spike_fires`, `test_short_history_fires`, `test_recent_failures_fires`, `test_new_destination_fires`, `test_destination_reputation_fires`, `test_beneficiary_churn_fires`, `test_structuring_fires`, `test_stale_state_fires_and_is_reported` |
| A clean payment fires nothing and is allowed | Automated | `test_clean_payment_fires_nothing_and_is_allowed` |
| Score is the sum of fired weights, capped at 100 | Automated | `test_score_is_the_sum_of_fired_weights`, `test_score_is_capped_at_100`, `test_reasons_reconstruct_the_score` |
| Score maps to the band exactly at the boundaries | Automated | `test_band_boundaries`, `test_reputation_alone_reaches_review`, `test_combination_reaches_block` |
| Thresholds and weights are configuration | Automated | `test_thresholds_are_configurable` |
| Evaluation is idempotent on evaluation_id | Automated | `test_evaluation_is_idempotent`, `test_reused_id_with_different_request_conflicts`, `test_evaluate_is_idempotent`, `test_reused_id_with_different_body_conflicts` |
| The decision scores a feature snapshot from state | Automated | `test_established_clean_payment_is_allowed`, `test_high_value_reviews`, `test_consumed_state_feeds_a_decision` |
| A decision emits exactly one risk event | Automated | `test_emits_a_risk_evaluated_event` |
| The push consumer records observations, builds state, dedups, rejects malformed | Automated | `test_payment_event_records_observation_and_builds_state`, `test_failure_event_records_a_failure`, `test_push_endpoint_decodes_and_dedups`, `test_push_endpoint_rejects_malformed` |
| The outbox relay publishes at-least-once and preserves order | Automated | `test_envelope_has_the_full_abs_contract`, `test_publish_marks_rows_published`, `test_failed_publish_leaves_everything_pending` |
| The ORM and the migration agree on the schema | Automated | `test_decision_round_trips`, `test_evaluation_id_is_unique`, `test_observation_dedup_on_event_id`, `test_account_state_defaults_apply`, `test_outbox_round_trips_and_starts_unpublished` (all run against the migrated schema) |
| API contract: evaluate, retrieve, validation, not found | Automated | `test_evaluate_returns_a_decision`, `test_get_decision`, `test_invalid_amount_rejected`, `test_get_missing_decision_404`, `test_health` |
| Lint and infrastructure validity | CI evidence | ruff on push; `terraform fmt`, `init`, `validate` in the Terraform workflow |

## Live Verification

Driven against the deployed engine and the rest of the live ecosystem:

- `GET /health` returns 200 with the database connected, and migrations run on container start.
- A `POST /risk/evaluate` for a high-value payment to a new destination returned `review` at score 63 with the reasons and the config hash; a small payment returned `allow` at score 38.
- The async state path works: consuming a `payment.received` for an account removed `NEW_DESTINATION` from its next decision.
- The Pub/Sub push subscription delivers real orchestrator payment events to `POST /events/pubsub`, every delivery returning 200.
- The orchestrator's payment decision comes from the engine: a live 6000 payment was held in RISK_REVIEW with no reservation, and the engine's persisted decision carried the same correlation_id as the payment.

## Acceptance Criteria

The risk engine passes V&V when:

- Every ABS requirement it owns has an automated test, and every deterministic behaviour is additionally shown live.
- CI is green: ruff, the full suite against the Alembic-migrated PostgreSQL schema, and Terraform validation.
- The live `/health` returns 200 and the engine returns explainable decisions that the orchestrator acts on, with uncertainty (unreachable engine or stale state) resolving to a hold, never to an allow.
