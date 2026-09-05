"""The consumer records observations, dedups, builds state, and feeds decisions."""

import base64
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.models import AccountState, RiskObservation
from app.observations import apply_observation
from app.schemas import EvaluateRequest
from app.service import evaluate_payment


def _envelope(event_type="payment.received", account_id=None, amount="100.00",
              destination="acme", occurred_at=None, event_id=None):
    account_id = account_id or str(uuid.uuid4())
    when = (occurred_at or datetime.now(tz=timezone.utc)).isoformat()
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": when,
        "producer": "payment-orchestrator",
        "correlation_id": str(uuid.uuid4()),
        "causation_id": str(uuid.uuid4()),
        "aggregate_id": str(uuid.uuid4()),
        "payload": {
            "payment_id": str(uuid.uuid4()),
            "account_id": account_id,
            "amount": amount,
            "destination": destination,
        },
    }


def test_payment_event_records_observation_and_builds_state(db):
    acc = str(uuid.uuid4())
    env = _envelope(account_id=acc, amount="200.00", destination="acme")
    assert apply_observation(db, env) == "processed"

    assert db.get(RiskObservation, uuid.UUID(env["event_id"])) is not None
    state = db.get(AccountState, uuid.UUID(acc))
    assert state is not None
    assert json.loads(state.seen_destinations) == ["acme"]
    assert state.amount_count == 1
    assert state.amount_sum == Decimal("200.00")
    assert len(json.loads(state.recent_payments)) == 1


def test_duplicate_event_is_a_noop(db):
    acc = str(uuid.uuid4())
    env = _envelope(account_id=acc, amount="200.00")
    assert apply_observation(db, env) == "processed"
    assert apply_observation(db, env) == "duplicate"
    state = db.get(AccountState, uuid.UUID(acc))
    assert state.amount_count == 1  # not double counted


def test_failure_event_records_a_failure(db):
    acc = str(uuid.uuid4())
    env = _envelope(event_type="payment.failed", account_id=acc)
    apply_observation(db, env)
    state = db.get(AccountState, uuid.UUID(acc))
    assert len(json.loads(state.recent_failures)) == 1


def test_consumed_state_feeds_a_decision(db):
    acc = str(uuid.uuid4())
    # Five recent payments build a velocity spike.
    for _ in range(5):
        apply_observation(db, _envelope(account_id=acc, amount="100.00"))
    req = EvaluateRequest(
        evaluation_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        account_id=uuid.UUID(acc),
        amount=Decimal("100.00"),
        destination="acme",
        correlation_id=uuid.uuid4(),
    )
    decision = evaluate_payment(db, req)
    assert "VELOCITY_SPIKE" in [r["rule"] for r in json.loads(decision.reasons)]


def test_push_endpoint_decodes_and_dedups(client):
    env = _envelope()
    data = base64.b64encode(json.dumps(env).encode()).decode()
    body = {"message": {"data": data, "messageId": "1"}, "subscription": "s"}
    first = client.post("/events/pubsub", json=body)
    second = client.post("/events/pubsub", json=body)
    assert first.status_code == 200 and first.json()["status"] == "processed"
    assert second.status_code == 200 and second.json()["status"] == "duplicate"


def test_push_endpoint_rejects_malformed(client):
    body = {"message": {"data": "not-base64-json!!", "messageId": "1"}}
    resp = client.post("/events/pubsub", json=body)
    assert resp.status_code == 400
