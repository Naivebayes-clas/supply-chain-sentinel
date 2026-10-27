"""Basic API tests."""
from fastapi.testclient import TestClient
import sys
sys.path.insert(0, '.')

def test_health():
    from api.main import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_query_endpoint():
    from api.main import app
    client = TestClient(app)
    resp = client.post("/query", params={"question": "How many shipments this week?"})
    assert resp.status_code == 200
    assert "answer" in resp.json()
