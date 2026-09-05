"""The outbox relay wraps rows in the ABS envelope and publishes at-least-once."""

import json
import uuid

from app.models import OutboxEvent
from app.relay import build_envelope, pending_events, publish_pending


class FakeTransport:
    name = "fake"

    def __init__(self, fail_after=None):
        self.published = []
        self.fail_after = fail_after

    def publish(self, envelope):
        if self.fail_after is not None and len(self.published) >= self.fail_after:
            raise RuntimeError("transport down")
        self.published.append(envelope)
        return f"fake:{envelope['event_id']}"


def _outbox(db, **overrides):
    base = dict(
        aggregate_type="risk_decision",
        aggregate_id=uuid.uuid4(),
        event_type="risk.evaluated",
        payload=json.dumps({"decision": "review"}),
        correlation_id=uuid.uuid4(),
        causation_id=uuid.uuid4(),
    )
    base.update(overrides)
    row = OutboxEvent(**base)
    db.add(row)
    db.commit()
    return row


def test_envelope_has_the_full_abs_contract(db):
    row = _outbox(db)
    env = build_envelope(row)
    assert set(env) == {
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "producer",
        "correlation_id",
        "causation_id",
        "aggregate_id",
        "payload",
    }
    assert env["producer"] == "risk-engine"
    assert env["event_id"] == str(row.id)
    assert env["payload"] == {"decision": "review"}


def test_publish_marks_rows_published(db):
    _outbox(db)
    _outbox(db)
    transport = FakeTransport()
    result = publish_pending(db, transport)
    assert result == {"published": 2, "failed": 0, "transport": "fake"}
    assert pending_events(db) == []


def test_failed_publish_leaves_everything_pending(db):
    _outbox(db)
    _outbox(db)
    _outbox(db)
    transport = FakeTransport(fail_after=1)
    result = publish_pending(db, transport)
    assert result["published"] == 1
    assert result["failed"] == 2
    # The two unpublished rows are still pending for the next call.
    assert len(pending_events(db)) == 2


def test_event_id_is_stable_for_consumer_dedup(db):
    row = _outbox(db)
    assert build_envelope(row)["event_id"] == build_envelope(row)["event_id"]
