import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class RiskDecision(Base):
    """Append-only log of every decision. Unique on `evaluation_id`, which is
    what makes evaluation idempotent: a retry with the same id returns this row
    rather than creating another. The feature snapshot and the config hash make
    the decision replayable long after it was made."""

    __tablename__ = "risk_decisions"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_evaluation_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), nullable=False)
    payment_id = Column(UUID(as_uuid=True), nullable=False)
    account_id = Column(UUID(as_uuid=True), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    destination = Column(String, nullable=False)

    # The derived features scored, stored so the decision replays exactly.
    feature_snapshot = Column(Text, nullable=False)
    rule_version = Column(String, nullable=False)
    rule_config_hash = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    # "allow" | "review" | "block". A plain string, deliberately not a database
    # enum: the orchestrator's one live failure was an enum name/value mismatch,
    # and a string carries the same three values with none of that risk.
    decision = Column(String, nullable=False)
    reasons = Column(Text, nullable=False)

    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RiskObservation(Base):
    """One row per consumed event: the evidence behavioural state is derived
    from. `event_id` is the primary key, so a redelivered event is rejected by
    the database (ABS-REQ-008). `occurred_at` lets state be ordered by business
    time rather than by Pub/Sub arrival order."""

    __tablename__ = "risk_observations"

    event_id = Column(UUID(as_uuid=True), primary_key=True)
    event_type = Column(String(100), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    account_id = Column(UUID(as_uuid=True), nullable=True)
    payment_id = Column(UUID(as_uuid=True), nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    payload = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class AccountState(Base):
    """Derived cache of an account's behavioural state, rebuildable from
    observations. Never authoritative; it only informs the score. Rolling
    windows are kept as JSON so velocity, churn and failure counts can be
    computed for the window in force at evaluate time."""

    __tablename__ = "account_state"

    account_id = Column(UUID(as_uuid=True), primary_key=True)
    first_observed_at = Column(DateTime(timezone=True), nullable=False)
    recent_payments = Column(Text, nullable=False, default="[]")
    recent_failures = Column(Text, nullable=False, default="[]")
    seen_destinations = Column(Text, nullable=False, default="[]")
    recent_destination_changes = Column(Text, nullable=False, default="[]")
    amount_sum = Column(Numeric(16, 2), nullable=False, default=0)
    amount_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEvent(Base):
    """Transactional outbox. A risk event is written in the same transaction as
    the decision it describes, so it exists if and only if that decision
    committed."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_published", "published_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type = Column(String(50), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String(100), nullable=False)
    event_version = Column(Integer, nullable=False, default=1)
    payload = Column(Text, nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    causation_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
