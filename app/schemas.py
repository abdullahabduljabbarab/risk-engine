from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, field_validator


class EvaluateRequest(BaseModel):
    # The orchestrator supplies evaluation_id so the call is idempotent: a retry
    # with the same id returns the original decision rather than making another.
    evaluation_id: UUID
    payment_id: UUID
    account_id: UUID
    amount: Decimal
    destination: str
    correlation_id: UUID | None = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class Reason(BaseModel):
    rule: str
    weight: int


class EvaluateResponse(BaseModel):
    decision_id: UUID
    evaluation_id: UUID
    decision: str
    band: str
    score: int
    rule_version: str
    rule_config_hash: str
    reasons: list[Reason]
    state_age_seconds: float
    correlation_id: UUID
    evaluated_at: datetime
