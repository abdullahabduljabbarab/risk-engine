"""The rules engine is deterministic, explainable, and scores each signal.

BASELINE is a clean payment from an established account to a known destination:
it fires no rule. Each test flips one signal and asserts the rule it targets.
"""

from dataclasses import replace
from decimal import Decimal

from app.decision import Decision
from app.rules import RiskConfig, RiskInput, evaluate

BASELINE = RiskInput(
    amount=Decimal("100"),
    destination="acme",
    account_age_hours=1000.0,
    seen_destinations=frozenset({"acme"}),
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


def test_amount_anomaly_fires_against_account_typical():
    ev = evaluate(replace(BASELINE, amount=Decimal("600"), typical_amount=Decimal("100")))
    assert "AMOUNT_ANOMALY" in _codes(ev)


def test_velocity_spike_fires():
    assert "VELOCITY_SPIKE" in _codes(evaluate(replace(BASELINE, velocity_count=5)))


def test_new_account_fires():
    assert "NEW_ACCOUNT" in _codes(evaluate(replace(BASELINE, account_age_hours=1.0)))


def test_recent_failures_fires():
    assert "RECENT_FAILURES" in _codes(evaluate(replace(BASELINE, recent_failures=3)))


def test_new_destination_fires():
    assert "NEW_DESTINATION" in _codes(evaluate(replace(BASELINE, destination="brand-new-co")))


def test_destination_reputation_fires():
    assert "DESTINATION_REPUTATION" in _codes(
        evaluate(replace(BASELINE, destination_on_watchlist=True))
    )


def test_beneficiary_churn_fires():
    assert "BENEFICIARY_CHURN" in _codes(evaluate(replace(BASELINE, beneficiary_changes=4)))


def test_structuring_fires():
    assert "STRUCTURING" in _codes(evaluate(replace(BASELINE, structuring_count=3)))


def test_score_is_the_sum_of_fired_weights():
    ev = evaluate(replace(BASELINE, amount=Decimal("6000"), velocity_count=5))
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
            structuring_count=3,
        )
    )
    assert ev.score == 95  # REPUTATION 40 + HIGH_VALUE 25 + STRUCTURING 30
    assert ev.decision is Decision.BLOCK


def test_score_is_capped_at_100():
    ev = evaluate(
        replace(
            BASELINE,
            amount=Decimal("6000"),
            typical_amount=Decimal("100"),
            velocity_count=5,
            account_age_hours=1.0,
            recent_failures=3,
            destination="new-co",
            destination_on_watchlist=True,
            beneficiary_changes=4,
            structuring_count=3,
        )
    )
    assert ev.score == 100  # every rule fires; sum exceeds 100 and is capped
    assert ev.decision is Decision.BLOCK


def test_reasons_reconstruct_the_score():
    ev = evaluate(replace(BASELINE, amount=Decimal("6000"), velocity_count=5))
    assert sum(r["weight"] for r in ev.reasons) == ev.score


def test_same_input_and_version_is_replayable():
    inp = replace(BASELINE, amount=Decimal("6000"), velocity_count=5)
    a = evaluate(inp)
    b = evaluate(inp)
    assert (a.score, a.decision, a.reasons, a.rule_version) == (
        b.score,
        b.decision,
        b.reasons,
        b.rule_version,
    )


def test_thresholds_are_configurable():
    # Lowering the block boundary turns the same score into a block.
    inp = replace(BASELINE, destination_on_watchlist=True)  # score 40
    lenient = evaluate(inp, RiskConfig())
    strict = evaluate(inp, RiskConfig(block_at=40))
    assert lenient.decision is Decision.REVIEW
    assert strict.decision is Decision.BLOCK
