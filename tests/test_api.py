"""End-to-end mock loop through the HTTP API against a live FalkorDB.

Claude "finishes" a task -> API decides to call -> mock call is answered -> curator
writes to the graph -> a fresh Codex session reads the decision with its source call."""

from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("SUMMON_VOICE", "mock")
os.environ["SUMMON_DEVELOPER_ID"] = f"test_{uuid.uuid4().hex[:6]}"
os.environ["SUMMON_API_TOKEN"] = "t"

from summon.graph.schema import get_db, graph_name, ping

pytestmark = pytest.mark.skipif(not ping(), reason="no FalkorDB reachable")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from summon.api.app import app

    with TestClient(app) as c:
        yield c
    get_db().select_graph(graph_name(os.environ["SUMMON_DEVELOPER_ID"])).delete()


H = {"Authorization": "Bearer t"}


def test_full_mock_loop(client):
    r = client.post("/api/sessions", json={"agent": "claude-code", "project": "demo",
                                           "session_id": "s1"}, headers=H)
    assert r.status_code == 200

    r = client.post("/api/calls", json={
        "project": "demo", "agent": "claude-code", "session_id": "s1", "task_id": "t1",
        "title": "Auth refactor", "summary": "Moved login to /auth/login. Tests pass.",
        "questions": ["Keep the old /login route?"], "files": ["api/auth.py"],
    }, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "call" and body["provider"] == "mock"
    call_id = body["call_id"]
    assert body["lines"][0][0] == "greet" and "Summon" in body["lines"][0][1]

    # idempotent: same task again returns the existing call, no second dial
    r2 = client.post("/api/calls", json={
        "project": "demo", "agent": "claude-code", "session_id": "s1", "task_id": "t1",
        "title": "Auth refactor", "summary": "x", "questions": []}, headers=H)
    assert r2.json()["action"] == "existing"

    # still pending
    r = client.get(f"/api/calls/{call_id}", params={"wait": 0})
    assert r.json()["status"] == "placed"

    # the developer answers the phone
    r = client.post("/api/mock/reply", json={
        "call_id": call_id,
        "answers": ["Yes, keep it backward compatible."],
        "more": "Add a deprecation header to the old route.",
    })
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "analyzed"
    assert v["answers"][0]["answer"].startswith("Yes")
    assert v["instructions"][0]["text"].startswith("Add a deprecation")
    assert v["decisions"], "a 'keep' answer should become a decision"

    # a different agent, fresh session, reads the memory with provenance
    client.post("/api/sessions", json={"agent": "codex", "project": "demo",
                                       "session_id": "s2"}, headers=H)
    h = client.get("/api/memory/handoff", params={"project": "demo", "agent": "codex"}).json()
    assert h["tasks_by_others"][0]["by_agent"] == "claude-code"
    assert h["decisions"][0]["call_id"] == call_id
    p = client.get(f"/api/memory/provenance/{h['decisions'][0]['id']}").json()
    assert p["while_agent"] == "claude-code" and p["said_on"]
    assert any(r["agent"] == "codex" for r in p["read_by"])

    hits = client.get("/api/memory/recall", params={"q": "backward compatible"}).json()["hits"]
    assert hits and hits[0]["label"] == "Decision"

    tl = client.get("/api/memory/timeline").json()
    assert {n["label"] for n in tl["nodes"]} >= {"Developer", "Task", "Call", "Decision",
                                                  "Instruction", "Agent", "CallDecision"}


def test_token_required(client):
    assert client.post("/api/calls", json={}).status_code == 401


def test_webhook_signature_and_idempotency(client, monkeypatch):
    """A signed call.analyzed for a placed call is applied once; a redelivery is a no-op."""
    import json

    from summon.config import get_settings
    from summon.voice.shravan import sign

    monkeypatch.setattr(get_settings(), "shravan_webhook_secret", "whsec_test")
    client.post("/api/sessions", json={"agent": "claude-code", "project": "demo",
                                       "session_id": "s9"}, headers=H)
    r = client.post("/api/calls", json={
        "project": "demo", "agent": "claude-code", "session_id": "s9", "task_id": "t9",
        "title": "Webhook task", "summary": "Did a thing.", "questions": ["Ship it?"]},
        headers=H)
    call_id = r.json()["call_id"]
    provider_id = r.json().get("provider_call_id") or f"mock-{call_id}"
    evt = {"id": "evt_dup_1", "object": "event", "type": "call.analyzed", "created": 1,
           "livemode": False, "data": {"object": {
               "id": provider_id, "status": "completed", "direction": "outbound",
               "duration_seconds": 30, "disposition": "information_provided",
               "metadata": {"summon_call_id": call_id},
               "transcript": [
                   {"role": "assistant", "content": "Ship it?", "ts": "t", "node_id": "ask_q1"},
                   {"role": "user", "content": "Yes, ship it to staging first.", "ts": "t",
                    "node_id": "ask_q1"},
                   {"role": "assistant", "content": "Anything else?", "ts": "t", "node_id": "ask_more"},
                   {"role": "user", "content": "Bump the version after.", "ts": "t",
                    "node_id": "ask_more"}]}}}
    body = json.dumps(evt, separators=(",", ":"), sort_keys=True).encode()
    bad = client.post("/api/webhooks/shravan", content=body,
                      headers={"Shravan-Signature": "t=1,v1=00", "Content-Type": "application/json"})
    assert bad.status_code == 401
    hdr = {"Shravan-Signature": sign(body, "whsec_test"), "Content-Type": "application/json"}
    assert client.post("/api/webhooks/shravan", content=body, headers=hdr).json() == {"ok": True}
    dup = client.post("/api/webhooks/shravan", content=body, headers=hdr).json()
    assert dup.get("duplicate") is True
    v = client.get(f"/api/calls/{call_id}").json()
    assert v["status"] == "analyzed"
    assert len(v["instructions"]) == 1 and len(v["answers"]) == 1
