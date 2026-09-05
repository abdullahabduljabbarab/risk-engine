"""The models round-trip against the Alembic-migrated schema.

Because conftest builds the schema from the migrations (ADR-014), these tests
also assert that the ORM and the migration agree: an insert that the ORM emits
must be accepted by the migrated tables.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AccountState, OutboxEvent, RiskDecision, RiskObservation


def _decision(**overrides):
    base = dict(
        id=uuid.uuid4(),
        evaluation_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        amount="250.00",
        destination="acme",
        feature_snapshot=json.dumps({"amount": "250.00", "state_age_seconds": 3}),
        rule_version="2026.09.1",
        rule_config_hash="abc123",
        score=68,
        decision="review",
        reasons=json.dumps([{"rule": "HIGH_VALUE", "weight": 25}]),
        correlation_id=uuid.uuid4(),
    )
    base.update(overrides)
    return RiskDecision(**base)


def test_decision_round_trips(db):
    d = _decision()
    db.add(d)
    db.commit()
    got = db.get(RiskDecision, d.id)
    assert got.decision == "review"
    assert got.score == 68
    assert got.rule_config_hash == "abc123"
    assert got.created_at is not None


def test_evaluation_id_is_unique(db):
    shared = uuid.uuid4()
    db.add(_decision(evaluation_id=shared))
    db.commit()
    db.add(_decision(evaluation_id=shared))
    with pytest.raises(IntegrityError):
        db.commit()


def test_observation_dedup_on_event_id(db):
    event_id = uuid.uuid4()
    obs = RiskObservation(
        event_id=event_id,
        event_type="payment.settled",
        occurred_at=datetime.now(tz=timezone.utc),
        payload="{}",
    )
    db.add(obs)
    db.commit()
    # Insert the duplicate from a separate session so it reaches the database
    # primary-key constraint rather than the session identity map.
    other = Session(bind=db.get_bind())
    try:
        other.add(
            RiskObservation(
                event_id=event_id,
                event_type="payment.settled",
                occurred_at=datetime.now(tz=timezone.utc),
                payload="{}",
            )
        )
        with pytest.raises(IntegrityError):
            other.commit()
    finally:
        other.close()


def test_account_state_defaults_apply(db):
    account_id = uuid.uuid4()
    state = AccountState(
        account_id=account_id,
        first_observed_at=datetime.now(tz=timezone.utc),
    )
    db.add(state)
    db.commit()
    got = db.get(AccountState, account_id)
    assert json.loads(got.recent_payments) == []
    assert json.loads(got.seen_destinations) == []
    assert got.amount_count == 0


def test_outbox_round_trips_and_starts_unpublished(db):
    ev = OutboxEvent(
        aggregate_type="risk_decision",
        aggregate_id=uuid.uuid4(),
        event_type="risk.evaluated",
        payload="{}",
        correlation_id=uuid.uuid4(),
        causation_id=uuid.uuid4(),
    )
    db.add(ev)
    db.commit()
    got = db.get(OutboxEvent, ev.id)
    assert got.event_version == 1
    assert got.published_at is None
