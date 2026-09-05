"""The score-to-band mapping is exact at the configured boundaries."""

import pytest

from app.decision import Decision, band_for_score

REVIEW_AT = 40
BLOCK_AT = 70


@pytest.mark.parametrize(
    "score, expected",
    [
        (0, Decision.ALLOW),
        (39, Decision.ALLOW),
        (40, Decision.REVIEW),
        (69, Decision.REVIEW),
        (70, Decision.BLOCK),
        (100, Decision.BLOCK),
    ],
)
def test_band_boundaries(score, expected):
    assert band_for_score(score, REVIEW_AT, BLOCK_AT) is expected
