"""Initial schema: decisions, observations, account state, outbox

Revision ID: 001
Revises:
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("feature_snapshot", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.String(), nullable=False),
        sa.Column("rule_config_hash", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("evaluation_id", name="uq_evaluation_id"),
    )

    op.create_table(
        "risk_observations",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "account_state",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recent_payments", sa.Text(), nullable=False),
        sa.Column("recent_failures", sa.Text(), nullable=False),
        sa.Column("seen_destinations", sa.Text(), nullable=False),
        sa.Column("recent_destination_changes", sa.Text(), nullable=False),
        sa.Column("amount_sum", sa.Numeric(16, 2), nullable=False),
        sa.Column("amount_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_published", "outbox_events", ["published_at"])


def downgrade():
    op.drop_table("outbox_events")
    op.drop_table("account_state")
    op.drop_table("risk_observations")
    op.drop_table("risk_decisions")
