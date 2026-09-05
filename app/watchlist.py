"""The destination watch list behind DESTINATION_REPUTATION.

Kept versioned and separate so the reputation result is a simple, snapshotted
boolean at evaluate time, and the list version travels in the config hash. A
production system would load this from a maintained source; here it is a small
fixed set with a version, which is enough to exercise the rule.
"""

WATCHLIST_VERSION = "2026.09.1"

_WATCHLIST: frozenset[str] = frozenset(
    {
        "sanctioned-co",
        "known-fraud-dest",
    }
)


def on_watchlist(destination: str) -> bool:
    return destination in _WATCHLIST
