"""
Load test harness for the Risk Engine.

Run against the live GCP deployment:
    locust -f scripts/loadtest.py --host https://risk-engine-eppidgbmxa-nw.a.run.app

Then open http://localhost:8089 to configure users and start the test, or run
headless with -u / -r / -t. Set CA_BUNDLE to a certificate bundle if a local
TLS-inspecting proxy breaks certificate verification.
"""

import os
import random
import uuid

from locust import HttpUser, between, task

CA_BUNDLE = os.getenv("CA_BUNDLE")
DESTINATIONS = ["acme", "globex", "initech", "umbrella", "new-vendor"]


class RiskUser(HttpUser):
    wait_time = between(0.1, 0.5)
    last_evaluation_id = None

    def on_start(self):
        if CA_BUNDLE:
            self.client.verify = CA_BUNDLE

    @task(5)
    def evaluate(self):
        evaluation_id = str(uuid.uuid4())
        resp = self.client.post(
            "/risk/evaluate",
            json={
                "evaluation_id": evaluation_id,
                "payment_id": str(uuid.uuid4()),
                "account_id": str(uuid.uuid4()),
                "amount": f"{random.randint(10, 9000)}.00",
                "destination": random.choice(DESTINATIONS),
                "correlation_id": str(uuid.uuid4()),
            },
        )
        if resp.status_code == 200:
            self.last_evaluation_id = evaluation_id

    @task(2)
    def get_decision(self):
        if self.last_evaluation_id:
            self.client.get(
                f"/decisions/{self.last_evaluation_id}", name="/decisions/[id]"
            )

    @task(1)
    def health(self):
        self.client.get("/health")
