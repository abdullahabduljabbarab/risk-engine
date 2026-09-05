import base64
import json
import logging
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.logging import setup_logging
from app.models import RiskDecision
from app.observations import apply_observation
from app.publisher import get_transport
from app.relay import pending_events, publish_pending
from app.schemas import EvaluateRequest, EvaluateResponse
from app.service import EvaluationConflict, evaluate_payment

setup_logging()
logger = logging.getLogger("risk.api")

DESCRIPTION = """
Deterministic payment risk decisioning.

Given a payment, the engine returns **ALLOW**, **REVIEW** or **BLOCK** with a
score and the reasons behind it. It is the decision the payment orchestrator
waits on before it reserves any funds, so a risky payment is held before money
moves rather than after.

**Correctness properties**

- Scoring is a deterministic weighted rules engine, not a model: the same
  feature snapshot and rule version always produce the same decision
- Every decision records the snapshot, the rule version and a config hash, so it
  is explainable and replayable
- Evaluation is idempotent on a caller-supplied `evaluation_id`
- Uncertainty never permits money to move: state older than the maximum
  acceptable age is floored to review, and an unreachable engine holds a payment

**Two paths**

The synchronous decision (`POST /risk/evaluate`) reads behavioural state and
scores immediately. The asynchronous state is built from events, so a decision
is never blocked on the freshness of the feed, only informed by it.
"""

TAGS = [
    {"name": "System", "description": "Health and service status."},
    {"name": "Decisioning", "description": "Evaluate a payment and retrieve a decision."},
    {
        "name": "Event Delivery",
        "description": "Pub/Sub push consumer for behavioural state, and the outbox relay.",
    },
]

app = FastAPI(
    title="Risk Engine",
    version="0.1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)


def _to_response(d: RiskDecision) -> EvaluateResponse:
    snapshot = json.loads(d.feature_snapshot)
    return EvaluateResponse(
        decision_id=d.id,
        evaluation_id=d.evaluation_id,
        decision=d.decision,
        band=d.decision.upper(),
        score=d.score,
        rule_version=d.rule_version,
        rule_config_hash=d.rule_config_hash,
        reasons=json.loads(d.reasons),
        state_age_seconds=snapshot.get("state_age_seconds", 0.0),
        correlation_id=d.correlation_id,
        evaluated_at=d.created_at,
    )


@app.get("/health", tags=["System"], summary="Liveness and database probe")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post(
    "/risk/evaluate",
    response_model=EvaluateResponse,
    tags=["Decisioning"],
    summary="Evaluate a payment",
)
def evaluate_endpoint(req: EvaluateRequest, db: Session = Depends(get_db)):
    try:
        decision = evaluate_payment(db, req)
    except EvaluationConflict as e:
        raise HTTPException(
            status_code=409,
            detail=f"evaluation_id already used with a different request: {e}",
        )
    return _to_response(decision)


@app.get(
    "/decisions/{evaluation_id}",
    response_model=EvaluateResponse,
    tags=["Decisioning"],
    summary="Retrieve a decision by evaluation_id",
)
def get_decision(evaluation_id: UUID, db: Session = Depends(get_db)):
    decision = db.execute(
        select(RiskDecision).where(RiskDecision.evaluation_id == evaluation_id)
    ).scalar_one_or_none()
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return _to_response(decision)


class PubSubPush(BaseModel):
    message: dict
    subscription: str | None = None


@app.post(
    "/events/pubsub",
    tags=["Event Delivery"],
    summary="Pub/Sub push endpoint for behavioural state",
)
def pubsub_push(body: PubSubPush, db: Session = Depends(get_db)):
    data = body.message.get("data")
    if not data:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        envelope = json.loads(base64.b64decode(data).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid message data")
    if not all(k in envelope for k in ("event_id", "event_type", "occurred_at")):
        raise HTTPException(status_code=400, detail="invalid envelope")
    status = apply_observation(db, envelope)
    return {"status": status}


@app.get(
    "/outbox/pending",
    tags=["Event Delivery"],
    summary="List unpublished events",
)
def outbox_pending(
    limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)
):
    rows = pending_events(db, limit)
    return {
        "pending_count": len(rows),
        "events": [
            {
                "event_id": str(r.id),
                "event_type": r.event_type,
                "aggregate_id": str(r.aggregate_id),
                "correlation_id": str(r.correlation_id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@app.post(
    "/outbox/publish",
    tags=["Event Delivery"],
    summary="Relay pending events to the broker",
)
def outbox_publish(
    limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)
):
    return publish_pending(db, get_transport(), limit)
