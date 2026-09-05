"""The deterministic weighted rules engine.

Scoring is a pure function of an input snapshot and a configuration. Each rule
inspects the snapshot and either fires or does not; a fired rule contributes its
configured weight and a reason code. The score is the sum of fired weights,
capped at 100, and the decision is the band the score falls in.

Determinism is the point: the same snapshot and the same config always produce
the same score, band and reasons (ABS-REQ-014), and the reasons fully explain
the score (ABS-REQ-015). Nothing here reads the clock or any external state; the
behavioural signals arrive already snapshotted on the input.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.decision import Decision, band_for_score

# Default rule weights. These are configuration, not code: tuning risk is
# changing a weight and bumping the rule version, never editing a rule.
DEFAULT_WEIGHTS: dict[str, int] = {
    "HIGH_VALUE": 25,
    "AMOUNT_ANOMALY": 20,
    "VELOCITY_SPIKE": 25,
    "NEW_ACCOUNT": 20,
    "RECENT_FAILURES": 20,
    "NEW_DESTINATION": 18,
    "DESTINATION_REPUTATION": 40,
    "BENEFICIARY_CHURN": 15,
    "STRUCTURING": 30,
}


@dataclass(frozen=True)
class RiskConfig:
    """The knobs that turn signals into a decision. A change to any of these is
    a new rule version, so a decision made under it stays replayable."""

    rule_version: str = "2026.09.1"
    weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    high_value_threshold: Decimal = Decimal("5000")
    anomaly_factor: Decimal = Decimal("5")
    velocity_threshold: int = 5
    new_account_hours: float = 24.0
    failures_threshold: int = 3
    churn_threshold: int = 4
    structuring_threshold: int = 3
    review_at: int = 40
    block_at: int = 70


@dataclass(frozen=True)
class RiskInput:
    """The snapshot a decision is made from. Payment fields plus the behavioural
    state the async path maintains. Defaults describe an established account with
    no adverse history, so a rule fires only when its signal is actually present."""

    amount: Decimal
    destination: str
    account_age_hours: float = 1_000_000.0  # established unless told otherwise
    velocity_count: int = 0
    recent_failures: int = 0
    seen_destinations: frozenset[str] = frozenset()
    destination_on_watchlist: bool = False
    typical_amount: Decimal | None = None
    beneficiary_changes: int = 0
    structuring_count: int = 0


@dataclass(frozen=True)
class Evaluation:
    score: int
    decision: Decision
    reasons: list[dict]
    rule_version: str


def _high_value(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.amount >= cfg.high_value_threshold


def _amount_anomaly(inp: RiskInput, cfg: RiskConfig) -> bool:
    return (
        inp.typical_amount is not None
        and inp.typical_amount > 0
        and inp.amount >= inp.typical_amount * cfg.anomaly_factor
    )


def _velocity_spike(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.velocity_count >= cfg.velocity_threshold


def _new_account(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.account_age_hours < cfg.new_account_hours


def _recent_failures(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.recent_failures >= cfg.failures_threshold


def _new_destination(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.destination not in inp.seen_destinations


def _destination_reputation(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.destination_on_watchlist


def _beneficiary_churn(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.beneficiary_changes >= cfg.churn_threshold


def _structuring(inp: RiskInput, cfg: RiskConfig) -> bool:
    return inp.structuring_count >= cfg.structuring_threshold


# The order here is the order reasons are reported in, so it is stable and part
# of the explainable output.
RULES: list[tuple[str, object]] = [
    ("HIGH_VALUE", _high_value),
    ("AMOUNT_ANOMALY", _amount_anomaly),
    ("VELOCITY_SPIKE", _velocity_spike),
    ("NEW_ACCOUNT", _new_account),
    ("RECENT_FAILURES", _recent_failures),
    ("NEW_DESTINATION", _new_destination),
    ("DESTINATION_REPUTATION", _destination_reputation),
    ("BENEFICIARY_CHURN", _beneficiary_churn),
    ("STRUCTURING", _structuring),
]


def evaluate(inp: RiskInput, cfg: RiskConfig | None = None) -> Evaluation:
    cfg = cfg or RiskConfig()
    reasons = [
        {"rule": code, "weight": cfg.weights[code]}
        for code, predicate in RULES
        if predicate(inp, cfg)
    ]
    score = min(100, sum(r["weight"] for r in reasons))
    decision = band_for_score(score, cfg.review_at, cfg.block_at)
    return Evaluation(
        score=score, decision=decision, reasons=reasons, rule_version=cfg.rule_version
    )
