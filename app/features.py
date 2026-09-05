"""Building a feature snapshot from persisted account state.

This is the one place that reads the clock and the stored state. It turns raw
rolling records into the derived, already-windowed features the rules engine
scores, and captures them so the decision replays exactly. The rules engine
downstream is a pure function of what this produces.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from app.models import AccountState
from app.rules import FeatureSnapshot, RiskConfig


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _count_ts_within(timestamps: list[str], now: datetime, window_seconds: float) -> int:
    return sum(1 for ts in timestamps if (now - _parse(ts)).total_seconds() <= window_seconds)


def _count_records_within(
    records: list[dict], now: datetime, window_seconds: float
) -> int:
    return sum(
        1 for r in records if (now - _parse(r["ts"])).total_seconds() <= window_seconds
    )


def build_snapshot(
    amount: Decimal,
    destination: str,
    state: AccountState | None,
    cfg: RiskConfig,
    on_watchlist: Callable[[str], bool],
    now: datetime,
    state_age_seconds: float,
) -> tuple[FeatureSnapshot, dict]:
    """Return the snapshot to score and the dict to persist. `state_age_seconds`
    is the age of the event feed as a whole (computed by the caller), not of this
    account, so a stopped feed is what makes state stale, not a quiet account."""

    on_wl = on_watchlist(destination)

    if state is None:
        snapshot = FeatureSnapshot(
            amount=amount,
            destination_seen_before=False,
            destination_on_watchlist=on_wl,
            observed_age_seconds=0.0,
            state_age_seconds=state_age_seconds,
        )
    else:
        recent_payments = json.loads(state.recent_payments or "[]")
        recent_failures = json.loads(state.recent_failures or "[]")
        seen = set(json.loads(state.seen_destinations or "[]"))
        changes = json.loads(state.recent_destination_changes or "[]")

        average = (
            (state.amount_sum / state.amount_count) if state.amount_count else None
        )
        structuring_count = sum(
            1
            for r in recent_payments
            if (now - _parse(r["ts"])).total_seconds() <= cfg.structuring_window_seconds
            and cfg.structuring_low <= Decimal(str(r["amount"])) < cfg.structuring_high
        )

        snapshot = FeatureSnapshot(
            amount=amount,
            destination_seen_before=destination in seen,
            destination_on_watchlist=on_wl,
            payment_count_window=_count_records_within(
                recent_payments, now, cfg.velocity_window_seconds
            ),
            failed_count_window=_count_ts_within(
                recent_failures, now, cfg.failures_window_seconds
            ),
            average_amount=average,
            beneficiary_count_window=_count_ts_within(
                changes, now, cfg.churn_window_seconds
            ),
            structuring_count_window=structuring_count,
            observed_age_seconds=max(
                0.0, (now - state.first_observed_at).total_seconds()
            ),
            state_age_seconds=state_age_seconds,
        )

    snapshot_dict = {
        "amount": str(snapshot.amount),
        "destination_seen_before": snapshot.destination_seen_before,
        "destination_on_watchlist": snapshot.destination_on_watchlist,
        "payment_count_window": snapshot.payment_count_window,
        "failed_count_window": snapshot.failed_count_window,
        "average_amount": (
            str(snapshot.average_amount) if snapshot.average_amount is not None else None
        ),
        "beneficiary_count_window": snapshot.beneficiary_count_window,
        "structuring_count_window": snapshot.structuring_count_window,
        "observed_age_seconds": snapshot.observed_age_seconds,
        "state_age_seconds": snapshot.state_age_seconds,
        "state_as_of": (now - timedelta(seconds=state_age_seconds)).isoformat(),
        "evaluation_time": now.isoformat(),
    }
    return snapshot, snapshot_dict
