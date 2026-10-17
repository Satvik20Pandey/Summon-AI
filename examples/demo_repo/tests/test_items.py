from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_items():
    assert client.get("/items").json()[0]["id"] == "1"


def test_version():
    body = client.get("/version").json()
    assert body["version"] == "0.1.0"
    assert isinstance(body["commit"], str)
    assert len(body["commit"]) == 7
