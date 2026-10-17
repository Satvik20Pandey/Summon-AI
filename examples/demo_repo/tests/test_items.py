from fastapi.testclient import TestClient

from app.main import ITEMS, app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_items():
    assert client.get("/items").json()[0]["id"] == "1"


def test_items_count():
    assert client.get("/items/count").json() == {"count": 1}


def test_get_item():
    assert client.get("/items/1").json() == {"id": "1", "name": "first"}


def test_get_item_missing():
    resp = client.get("/items/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


def test_delete_item():
    # Use a throwaway id so the shared "1" fixture other tests assert on survives.
    ITEMS["99"] = {"id": "99", "name": "doomed"}
    resp = client.delete("/items/99")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "99"}
    assert "99" not in ITEMS


def test_delete_item_missing():
    resp = client.delete("/items/99")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


def test_version():
    body = client.get("/version").json()
    assert body["version"] == "0.1.0"
    assert isinstance(body["commit"], str)
    assert len(body["commit"]) == 7
