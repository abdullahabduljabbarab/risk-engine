"""The API: evaluate, retrieve, idempotency and validation over HTTP."""

import uuid


def _body(**overrides):
    base = {
        "evaluation_id": str(uuid.uuid4()),
        "payment_id": str(uuid.uuid4()),
        "account_id": str(uuid.uuid4()),
        "amount": "100.00",
        "destination": "acme",
        "correlation_id": str(uuid.uuid4()),
    }
    base.update(overrides)
    return base


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_evaluate_returns_a_decision(client):
    body = _body(amount="6000.00")
    resp = client.post("/risk/evaluate", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] in ("allow", "review", "block")
    assert data["band"] == data["decision"].upper()
    assert data["evaluation_id"] == body["evaluation_id"]
    assert data["rule_config_hash"]
    assert isinstance(data["reasons"], list)


def test_evaluate_is_idempotent(client):
    body = _body()
    first = client.post("/risk/evaluate", json=body).json()
    second = client.post("/risk/evaluate", json=body).json()
    assert first["decision_id"] == second["decision_id"]


def test_reused_id_with_different_body_conflicts(client):
    eid = str(uuid.uuid4())
    client.post("/risk/evaluate", json=_body(evaluation_id=eid, amount="100.00"))
    resp = client.post("/risk/evaluate", json=_body(evaluation_id=eid, amount="6000.00"))
    assert resp.status_code == 409


def test_invalid_amount_rejected(client):
    resp = client.post("/risk/evaluate", json=_body(amount="0"))
    assert resp.status_code == 422


def test_get_decision(client):
    body = _body()
    client.post("/risk/evaluate", json=body)
    resp = client.get(f"/decisions/{body['evaluation_id']}")
    assert resp.status_code == 200
    assert resp.json()["evaluation_id"] == body["evaluation_id"]


def test_get_missing_decision_404(client):
    resp = client.get(f"/decisions/{uuid.uuid4()}")
    assert resp.status_code == 404
