"""The evaluate service: it scores from state, persists idempotently, and emits."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import AccountState, OutboxEvent, RiskDecision, RiskObservation
from app.schemas import EvaluateRequest
from app.service import EvaluationConflict, evaluate_payment


def _req(**overrides):
    base = dict(
        evaluation_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        destination="acme",
        correlation_id=uuid.uuid4(),
    )
    base.update(overrides)
    return EvaluateRequest(**base)


def _established_account(db, account_id, seen=("acme",)):
    now = datetime.now(tz=timezone.utc)
    state = AccountState(
        account_id=account_id,
        first_observed_at=now - timedelta(days=100),
        recent_payments="[]",
        recent_failures="[]",
        seen_destinations=json.dumps(list(seen)),
        recent_destination_changes="[]",
        amount_sum=Decimal("500.00"),
        amount_count=5,
        updated_at=now,
    )
    db.add(state)
    db.commit()


def test_new_account_small_payment_is_allowed(db):
    # No state: NEW_DESTINATION (18) + SHORT_HISTORY (20) = 38, still allow.
    d = evaluate_payment(db, _req())
    assert d.decision == "allow"
    assert db.query(RiskDecision).count() == 1


def test_high_value_reviews(db):
    d = evaluate_payment(db, _req(amount=Decimal("6000.00")))
    assert d.decision == "review"
    assert d.score >= 40


def test_established_clean_payment_is_allowed(db):
    acc = uuid.uuid4()
    _established_account(db, acc)
    d = evaluate_payment(db, _req(account_id=acc, amount=Decimal("100.00")))
    assert d.reasons == "[]" or json.loads(d.reasons) == []
    assert d.decision == "allow"


def test_evaluation_is_idempotent(db):
    req = _req()
    first = evaluate_payment(db, req)
    second = evaluate_payment(db, req)
    assert first.id == second.id
    assert db.query(RiskDecision).count() == 1
    # Only one event emitted for the one decision.
    assert db.query(OutboxEvent).count() == 1


def test_reused_id_with_different_request_conflicts(db):
    eid = uuid.uuid4()
    evaluate_payment(db, _req(evaluation_id=eid, amount=Decimal("100.00")))
    with pytest.raises(EvaluationConflict):
        evaluate_payment(db, _req(evaluation_id=eid, amount=Decimal("6000.00")))


def test_emits_a_risk_evaluated_event(db):
    req = _req()
    d = evaluate_payment(db, req)
    event = db.query(OutboxEvent).one()
    assert event.event_type == "risk.evaluated"
    assert event.correlation_id == d.correlation_id
    assert event.causation_id == req.evaluation_id
    payload = json.loads(event.payload)
    assert payload["decision"] == d.decision


def test_stale_feed_holds_an_otherwise_clean_payment(db):
    acc = uuid.uuid4()
    _established_account(db, acc)
    # An old observation makes the feed as a whole stale.
    old = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    db.add(
        RiskObservation(
            event_id=uuid.uuid4(),
            event_type="payment.settled",
            occurred_at=old,
            received_at=old,
            payload="{}",
        )
    )
    db.commit()
    d = evaluate_payment(db, _req(account_id=acc, amount=Decimal("100.00")))
    assert "STATE_STALE" in [r["rule"] for r in json.loads(d.reasons)]
    assert d.decision != "allow"


def test_decision_records_snapshot_and_config_hash(db):
    d = evaluate_payment(db, _req())
    snapshot = json.loads(d.feature_snapshot)
    assert "state_age_seconds" in snapshot
    assert "evaluation_time" in snapshot
    assert d.rule_config_hash
