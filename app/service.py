"""Evaluating a payment: build the snapshot, score it, and record the decision.

The decision and its outbox event are written in one transaction, so an event
exists if and only if the decision committed. Evaluation is idempotent on the
caller's evaluation_id: a retry returns the original decision and emits nothing
new, and the same id with a different request body is a conflict.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.features import build_snapshot
from app.models import AccountState, OutboxEvent, RiskDecision, RiskObservation
from app.rules import RiskConfig, evaluate
from app.schemas import EvaluateRequest
from app.watchlist import on_watchlist

logger = logging.getLogger("risk.service")


class EvaluationConflict(Exception):
    """The same evaluation_id was reused with a different request body."""


def _request_matches(existing: RiskDecision, req: EvaluateRequest) -> bool:
    return (
        existing.payment_id == req.payment_id
        and existing.account_id == req.account_id
        and Decimal(existing.amount) == req.amount
        and existing.destination == req.destination
    )


def _feed_age_seconds(db: Session, now: datetime) -> float:
    """How old the event feed is as a whole. A stopped feed is what makes state
    stale, so this is measured across all observations, not per account. No
    observations yet means a new system, treated as fresh."""
    last = db.execute(select(func.max(RiskObservation.received_at))).scalar()
    if last is None:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0.0, (now - last).total_seconds())


def _emit(db: Session, decision: RiskDecision, reasons: list[dict]) -> None:
    payload = {
        "decision_id": str(decision.id),
        "evaluation_id": str(decision.evaluation_id),
        "payment_id": str(decision.payment_id),
        "account_id": str(decision.account_id),
        "decision": decision.decision,
        "band": decision.decision.upper(),
        "score": decision.score,
        "reasons": reasons,
        "rule_version": decision.rule_version,
    }
    db.add(
        OutboxEvent(
            aggregate_type="risk_decision",
            aggregate_id=decision.id,
            event_type="risk.evaluated",
            payload=json.dumps(payload),
            correlation_id=decision.correlation_id,
            causation_id=decision.evaluation_id,
        )
    )


def evaluate_payment(
    db: Session,
    req: EvaluateRequest,
    cfg: RiskConfig | None = None,
    now: datetime | None = None,
) -> RiskDecision:
    cfg = cfg or RiskConfig()
    now = now or datetime.now(tz=timezone.utc)

    existing = db.execute(
        select(RiskDecision).where(RiskDecision.evaluation_id == req.evaluation_id)
    ).scalar_one_or_none()
    if existing is not None:
        if _request_matches(existing, req):
            return existing
        raise EvaluationConflict(str(req.evaluation_id))

    state = db.get(AccountState, req.account_id)
    state_age = _feed_age_seconds(db, now)
    snapshot, snapshot_dict = build_snapshot(
        req.amount, req.destination, state, cfg, on_watchlist, now, state_age
    )
    result = evaluate(snapshot, cfg)

    decision = RiskDecision(
        evaluation_id=req.evaluation_id,
        payment_id=req.payment_id,
        account_id=req.account_id,
        amount=req.amount,
        destination=req.destination,
        feature_snapshot=json.dumps(snapshot_dict),
        rule_version=result.rule_version,
        rule_config_hash=result.rule_config_hash,
        score=result.score,
        decision=result.decision.value,
        reasons=json.dumps(result.reasons),
        correlation_id=req.correlation_id or uuid4(),
    )
    db.add(decision)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent request with the same evaluation_id won the race; return
        # its decision rather than making a second one.
        db.rollback()
        existing = db.execute(
            select(RiskDecision).where(RiskDecision.evaluation_id == req.evaluation_id)
        ).scalar_one()
        if _request_matches(existing, req):
            return existing
        raise EvaluationConflict(str(req.evaluation_id)) from None

    _emit(db, decision, result.reasons)
    db.commit()
    db.refresh(decision)
    logger.info(
        f"decision {decision.decision} score {decision.score}",
        extra={"payment_id": str(decision.payment_id), "decision": decision.decision},
    )
    return decision
