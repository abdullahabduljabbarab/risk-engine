from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, field_validator


class EvaluateRequest(BaseModel):
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
    decision: str
    band: str
    score: int
    rule_version: str
    reasons: list[Reason]
    correlation_id: UUID
    evaluated_at: datetime
