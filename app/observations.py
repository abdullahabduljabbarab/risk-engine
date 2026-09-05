"""Applying a consumed event to behavioural state.

Every event is recorded as an observation (the evidence), deduplicated on
`event_id` so at-least-once redelivery is safe (ABS-REQ-008). Payment and failure
events also update the derived account_state, in the same transaction as the
observation, so state exists if and only if the observation was recorded. State
is a cache: it can always be rebuilt by replaying the observations.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AccountState, RiskObservation

logger = logging.getLogger("risk.observations")

# Every payment attempt emits payment.received, which is what velocity, novelty
# and amount signals are built from. Declines and rejections feed the failures
# signal.
PAYMENT_EVENTS = {"payment.received"}
FAILURE_EVENTS = {"payment.failed", "payment.rejected"}
RECENT_CAP = 100


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def apply_observation(db: Session, envelope: dict) -> str:
    """Record the observation and update state. Returns processed or duplicate."""
    event_id = UUID(envelope["event_id"])
    if db.get(RiskObservation, event_id) is not None:
        return "duplicate"

    event_type = envelope["event_type"]
    occurred_at = _parse_dt(envelope["occurred_at"])
    payload = envelope.get("payload") or {}
    account_id = payload.get("account_id")
    payment_id = payload.get("payment_id") or envelope.get("aggregate_id")
    correlation_id = envelope.get("correlation_id")

    db.add(
        RiskObservation(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            account_id=UUID(account_id) if account_id else None,
            payment_id=UUID(payment_id) if payment_id else None,
            correlation_id=UUID(correlation_id) if correlation_id else None,
            payload=json.dumps(payload),
        )
    )

    if account_id and (event_type in PAYMENT_EVENTS or event_type in FAILURE_EVENTS):
        _update_state(db, UUID(account_id), event_type, occurred_at, payload)

    try:
        db.commit()
    except IntegrityError:
        # A concurrent delivery of the same event won the race.
        db.rollback()
        return "duplicate"
    return "processed"


def _update_state(
    db: Session, account_id: UUID, event_type: str, occurred_at: datetime, payload: dict
) -> None:
    state = db.get(AccountState, account_id)
    if state is None:
        state = AccountState(
            account_id=account_id,
            first_observed_at=occurred_at,
            recent_payments="[]",
            recent_failures="[]",
            seen_destinations="[]",
            recent_destination_changes="[]",
            amount_sum=Decimal("0"),
            amount_count=0,
        )
        db.add(state)

    ts = occurred_at.isoformat()

    if event_type in FAILURE_EVENTS:
        failures = json.loads(state.recent_failures)
        failures.append(ts)
        state.recent_failures = json.dumps(failures[-RECENT_CAP:])
    else:
        amount = payload.get("amount")
        destination = payload.get("destination")

        payments = json.loads(state.recent_payments)
        payments.append({"ts": ts, "amount": str(amount), "destination": destination})
        state.recent_payments = json.dumps(payments[-RECENT_CAP:])

        if destination is not None:
            seen = json.loads(state.seen_destinations)
            if destination not in seen:
                seen.append(destination)
                state.seen_destinations = json.dumps(seen)
                changes = json.loads(state.recent_destination_changes)
                changes.append(ts)
                state.recent_destination_changes = json.dumps(changes[-RECENT_CAP:])

        if amount is not None:
            state.amount_sum = (state.amount_sum or Decimal("0")) + Decimal(str(amount))
            state.amount_count = (state.amount_count or 0) + 1

    # Keep the earliest observed time, so out-of-order delivery cannot make an
    # account look younger than it is.
    if occurred_at < state.first_observed_at:
        state.first_observed_at = occurred_at
