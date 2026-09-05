"""The three decisions the engine can return, and the score-to-band mapping.

A decision is derived purely from a score and the configured band boundaries, so
the mapping is a single, testable function with no hidden state.
"""

import enum


class Decision(str, enum.Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


def band_for_score(score: int, review_at: int, block_at: int) -> Decision:
    """Map a 0-100 score to a decision using the configured boundaries.

    A score at or above `block_at` blocks; at or above `review_at` reviews;
    below `review_at` allows. The boundaries are configuration, so tuning risk
    tolerance never touches this function.
    """
    if score >= block_at:
        return Decision.BLOCK
    if score >= review_at:
        return Decision.REVIEW
    return Decision.ALLOW
