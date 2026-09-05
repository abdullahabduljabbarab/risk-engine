import os

# The risk engine's own state (decisions, account behavioural state, outbox).
# Port 5434 locally, so the ledger (5432) and orchestrator (5433) can run
# alongside it without colliding.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://risk:risk@localhost:5434/risk",
)

# Event publishing. Unset means the log transport is used (see publisher).
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")

# The subscription the engine consumes payment and transaction events from to
# build behavioural state. Unset means the state consumer is not run.
PUBSUB_SUBSCRIPTION = os.getenv("PUBSUB_SUBSCRIPTION", "")

# When set, POST /events/pubsub requires a valid Google OIDC token minted for
# this service account, which an authenticated Pub/Sub push attaches. This stops
# an anonymous caller from posting forged events to skew behavioural state. Unset
# locally and in tests, where verification is skipped.
PUBSUB_PUSH_SA = os.getenv("PUBSUB_PUSH_SA", "")

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
