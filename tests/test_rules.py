"""The rules engine is deterministic, explainable, and scores each signal.

BASELINE is a clean payment from an established account to a known destination,
with fresh behavioural state: it fires no rule. Each test flips one feature and
asserts the rule it targets.
"""

from dataclasses import replace
from decimal import Decimal

from app.decision import Decision
from app.rules import FeatureSnapshot, RiskConfig, config_hash, evaluate

BASELINE = FeatureSnapshot(
    amount=Decimal("100"),
    destination_seen_before=True,
    observed_age_seconds=1_000_000.0,
    state_age_seconds=0.0,
)


def _codes(ev):
    return [r["rule"] for r in ev.reasons]


def test_clean_payment_fires_nothing_and_is_allowed():
    ev = evaluate(BASELINE)
    assert ev.reasons == []
    assert ev.score == 0
    assert ev.decision is Decision.ALLOW


def test_high_value_fires():
    assert "HIGH_VALUE" in _codes(evaluate(replace(BASELINE, amount=Decimal("6000"))))


def test_amount_anomaly_fires_against_account_average():
    ev = evaluate(replace(BASELINE, amount=Decimal("600"), average_amount=Decimal("100")))
    assert "AMOUNT_ANOMALY" in _codes(ev)


def test_velocity_spike_fires():
    assert "VELOCITY_SPIKE" in _codes(evaluate(replace(BASELINE, payment_count_window=5)))


def test_short_history_fires():
    assert "SHORT_HISTORY" in _codes(evaluate(replace(BASELINE, observed_age_seconds=3600.0)))


def test_recent_failures_fires():
    assert "RECENT_FAILURES" in _codes(evaluate(replace(BASELINE, failed_count_window=3)))


def test_new_destination_fires():
    assert "NEW_DESTINATION" in _codes(evaluate(replace(BASELINE, destination_seen_before=False)))


def test_destination_reputation_fires():
    assert "DESTINATION_REPUTATION" in _codes(
        evaluate(replace(BASELINE, destination_on_watchlist=True))
    )


def test_beneficiary_churn_fires():
    assert "BENEFICIARY_CHURN" in _codes(evaluate(replace(BASELINE, beneficiary_count_window=4)))


def test_structuring_fires():
    assert "STRUCTURING" in _codes(evaluate(replace(BASELINE, structuring_count_window=3)))


def test_score_is_the_sum_of_fired_weights():
    ev = evaluate(replace(BASELINE, amount=Decimal("6000"), payment_count_window=5))
    assert ev.score == 50  # HIGH_VALUE 25 + VELOCITY_SPIKE 25
    assert ev.decision is Decision.REVIEW


def test_reputation_alone_reaches_review():
    ev = evaluate(replace(BASELINE, destination_on_watchlist=True))
    assert ev.score == 40
    assert ev.decision is Decision.REVIEW


def test_combination_reaches_block():
    ev = evaluate(
        replace(
            BASELINE,
            destination_on_watchlist=True,
            amount=Decimal("6000"),
            structuring_count_window=3,
        )
    )
    assert ev.score == 95  # REPUTATION 40 + HIGH_VALUE 25 + STRUCTURING 30
    assert ev.decision is Decision.BLOCK


def test_score_is_capped_at_100():
    ev = evaluate(
        replace(
            BASELINE,
            amount=Decimal("6000"),
            average_amount=Decimal("100"),
            payment_count_window=5,
            observed_age_seconds=3600.0,
            failed_count_window=3,
            destination_seen_before=False,
            destination_on_watchlist=True,
            beneficiary_count_window=4,
            structuring_count_window=3,
        )
    )
    assert ev.score == 100  # every rule fires; sum exceeds 100 and is capped
    assert ev.decision is Decision.BLOCK


def test_stale_state_fires_and_is_reported():
    ev = evaluate(replace(BASELINE, state_age_seconds=7200.0))
    assert "STATE_STALE" in _codes(ev)


def test_stale_state_never_allows_even_when_otherwise_clean():
    # A clean payment on stale state would score 40 from STATE_STALE, but the
    # floor guarantees at least REVIEW regardless of the numeric outcome.
    ev = evaluate(replace(BASELINE, state_age_seconds=7200.0))
    assert ev.decision is Decision.REVIEW


def test_stale_floor_holds_even_if_weight_is_zeroed():
    # Even if STATE_STALE carried no weight, stale state must not allow.
    cfg = RiskConfig(weights={**RiskConfig().weights, "STATE_STALE": 0})
    ev = evaluate(replace(BASELINE, state_age_seconds=7200.0), cfg)
    assert ev.score == 0
    assert ev.decision is Decision.REVIEW


def test_reasons_reconstruct_the_score():
    ev = evaluate(replace(BASELINE, amount=Decimal("6000"), payment_count_window=5))
    assert sum(r["weight"] for r in ev.reasons) == ev.score


def test_same_snapshot_and_version_is_replayable():
    snap = replace(BASELINE, amount=Decimal("6000"), payment_count_window=5)
    a = evaluate(snap)
    b = evaluate(snap)
    assert (a.score, a.decision, a.reasons, a.rule_version, a.rule_config_hash) == (
        b.score,
        b.decision,
        b.reasons,
        b.rule_version,
        b.rule_config_hash,
    )


def test_thresholds_are_configurable():
    snap = replace(BASELINE, destination_on_watchlist=True)  # score 40
    lenient = evaluate(snap, RiskConfig())
    strict = evaluate(snap, RiskConfig(block_at=40))
    assert lenient.decision is Decision.REVIEW
    assert strict.decision is Decision.BLOCK


def test_config_hash_is_stable_and_sensitive():
    assert config_hash(RiskConfig()) == config_hash(RiskConfig())
    changed = RiskConfig(weights={**RiskConfig().weights, "HIGH_VALUE": 30})
    assert config_hash(changed) != config_hash(RiskConfig())
