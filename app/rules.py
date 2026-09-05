"""The deterministic weighted rules engine.

Scoring is a pure function of a feature snapshot and a configuration. Each rule
inspects the snapshot and either fires or does not; a fired rule contributes its
configured weight and a reason code. The score is the sum of fired weights,
capped at 100, and the decision is the band the score falls in.

Two properties make a decision auditable long after it was made:

- It scores a FeatureSnapshot: the derived, already-windowed features (counts in
  a window, an averaged amount, booleans), not raw state and never the clock. A
  replay of the same snapshot under the same rule version is exact (ABS-REQ-014).
- It carries the rule version and a hash of the full configuration, so the exact
  weights, thresholds, bands and watch-list version behind the decision are
  recoverable (ABS-REQ-015). A config change is a new version, never a silent
  mutation of an old one.

The fail-safe: if the behavioural state is too stale to trust, the STATE_STALE
rule fires and the decision is floored at REVIEW, so a lost event feed can never
let a payment be allowed from state the engine knows is out of date
(ABS-REQ-013, 016).
"""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from app.decision import Decision, band_for_score

# Default rule weights. Configuration, not code: tuning risk is changing a weight
# and bumping the rule version, never editing a rule.
DEFAULT_WEIGHTS: dict[str, int] = {
    "HIGH_VALUE": 25,
    "AMOUNT_ANOMALY": 20,
    "VELOCITY_SPIKE": 25,
    "SHORT_HISTORY": 20,
    "RECENT_FAILURES": 20,
    "NEW_DESTINATION": 18,
    "DESTINATION_REPUTATION": 40,
    "BENEFICIARY_CHURN": 15,
    "STRUCTURING": 30,
    "STATE_STALE": 40,
}


@dataclass(frozen=True)
class RiskConfig:
    """The knobs that turn features into a decision. A change to any of these is
    a new rule version, so a decision made under it stays replayable. The
    watch-list version is part of the config so DESTINATION_REPUTATION is
    replayable against the list that was in force."""

    rule_version: str = "2026.09.1"
    watchlist_version: str = "2026.09.1"
    weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    high_value_threshold: Decimal = Decimal("5000")
    anomaly_factor: Decimal = Decimal("5")
    velocity_threshold: int = 5
    short_history_seconds: float = 86_400.0  # 24h of observed history
    failures_threshold: int = 3
    churn_threshold: int = 4
    structuring_threshold: int = 3
    max_state_age_seconds: float = 3_600.0  # state older than this is not trusted
    review_at: int = 40
    block_at: int = 70


@dataclass(frozen=True)
class FeatureSnapshot:
    """The derived features a decision is scored from. Everything here is already
    computed against a window or an authority, so the engine reads no raw state
    and no clock. This is what gets persisted with the decision and replayed.

    Defaults describe an established account with no adverse signals and fresh
    state, so a rule fires only when its feature is actually present."""

    amount: Decimal
    destination_seen_before: bool = True
    destination_on_watchlist: bool = False
    payment_count_window: int = 0
    failed_count_window: int = 0
    average_amount: Decimal | None = None
    beneficiary_count_window: int = 0
    structuring_count_window: int = 0
    observed_age_seconds: float = 1_000_000_000.0  # long history unless told otherwise
    state_age_seconds: float = 0.0  # how old the behavioural state is at evaluate time


@dataclass(frozen=True)
class Evaluation:
    score: int
    decision: Decision
    reasons: list[dict]
    rule_version: str
    rule_config_hash: str


def _high_value(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.amount >= c.high_value_threshold


def _amount_anomaly(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return (
        f.average_amount is not None
        and f.average_amount > 0
        and f.amount >= f.average_amount * c.anomaly_factor
    )


def _velocity_spike(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.payment_count_window >= c.velocity_threshold


def _short_history(f: FeatureSnapshot, c: RiskConfig) -> bool:
    # Short observed history, not authoritative account age: the engine only
    # knows when it first observed the account, until ABS emits account.created.
    return f.observed_age_seconds < c.short_history_seconds


def _recent_failures(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.failed_count_window >= c.failures_threshold


def _new_destination(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return not f.destination_seen_before


def _destination_reputation(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.destination_on_watchlist


def _beneficiary_churn(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.beneficiary_count_window >= c.churn_threshold


def _structuring(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.structuring_count_window >= c.structuring_threshold


def _state_stale(f: FeatureSnapshot, c: RiskConfig) -> bool:
    return f.state_age_seconds > c.max_state_age_seconds


# The order here is the order reasons are reported in, so it is stable and part
# of the explainable output.
RULES: list[tuple[str, object]] = [
    ("HIGH_VALUE", _high_value),
    ("AMOUNT_ANOMALY", _amount_anomaly),
    ("VELOCITY_SPIKE", _velocity_spike),
    ("SHORT_HISTORY", _short_history),
    ("RECENT_FAILURES", _recent_failures),
    ("NEW_DESTINATION", _new_destination),
    ("DESTINATION_REPUTATION", _destination_reputation),
    ("BENEFICIARY_CHURN", _beneficiary_churn),
    ("STRUCTURING", _structuring),
    ("STATE_STALE", _state_stale),
]


def config_hash(cfg: RiskConfig) -> str:
    """A stable hash of the full configuration behind a decision. Two configs
    that would score identically hash identically; any change to a weight,
    threshold, band or the watch-list version changes the hash."""
    canonical = json.dumps(
        {
            "rule_version": cfg.rule_version,
            "watchlist_version": cfg.watchlist_version,
            "weights": cfg.weights,
            "high_value_threshold": str(cfg.high_value_threshold),
            "anomaly_factor": str(cfg.anomaly_factor),
            "velocity_threshold": cfg.velocity_threshold,
            "short_history_seconds": cfg.short_history_seconds,
            "failures_threshold": cfg.failures_threshold,
            "churn_threshold": cfg.churn_threshold,
            "structuring_threshold": cfg.structuring_threshold,
            "max_state_age_seconds": cfg.max_state_age_seconds,
            "review_at": cfg.review_at,
            "block_at": cfg.block_at,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def evaluate(snapshot: FeatureSnapshot, cfg: RiskConfig | None = None) -> Evaluation:
    cfg = cfg or RiskConfig()
    reasons = [
        {"rule": code, "weight": cfg.weights[code]}
        for code, predicate in RULES
        if predicate(snapshot, cfg)
    ]
    score = min(100, sum(r["weight"] for r in reasons))
    decision = band_for_score(score, cfg.review_at, cfg.block_at)

    # Fail-safe floor: state the engine knows is stale must never allow a payment,
    # whatever the numeric score works out to (ABS-REQ-013).
    if decision is Decision.ALLOW and any(r["rule"] == "STATE_STALE" for r in reasons):
        decision = Decision.REVIEW

    return Evaluation(
        score=score,
        decision=decision,
        reasons=reasons,
        rule_version=cfg.rule_version,
        rule_config_hash=config_hash(cfg),
    )
