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

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
