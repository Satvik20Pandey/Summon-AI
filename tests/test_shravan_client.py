"""Offline contract test for the Shravan.ai client: what `summon setup` and
`place_call` send, checked against the provider's documented limits."""

from __future__ import annotations

import json

import httpx
import pytest

from summon.config import get_settings
from summon.voice.base import CallScript
from summon.voice.shravan import ShravanVoice


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setattr(get_settings(), "shravan_api_key", "sk_test_abc")
    monkeypatch.setattr(get_settings(), "shravan_agent_id", "agent_x")
    seen: list[tuple[str, str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}") if req.content else {}
        seen.append((req.method, req.url.path, body))
        assert req.headers["authorization"] == "Bearer sk_test_abc"
        if req.url.path.endswith("/agents"):
            return httpx.Response(201, json={"id": "agent_" + body["flow_name"],
                                             "flow_id": "biz_" + body["flow_name"]})
        if req.url.path.endswith("/webhook_endpoints"):
            return httpx.Response(201, json={"id": "we_1", "signing_secret": "whsec_x"})
        if req.url.path.endswith("/calls") and req.method == "POST":
            assert req.headers["idempotency-key"]
            return httpx.Response(201, json={"id": "call_prov_1", "status": "queued",
                                             "livemode": False})
        if req.url.path.endswith("/calls/call_prov_1"):
            return httpx.Response(200, json={"id": "call_prov_1", "status": "in_progress"})
        return httpx.Response(404, json={"error": {"code": "resource_missing"}})

    return seen, httpx.MockTransport(handler)


async def test_setup_registers_two_agents_and_a_webhook(fake_provider):
    seen, transport = fake_provider
    out = await ShravanVoice(transport=transport).setup("https://summon.example/api/webhooks/shravan")
    posts = [(m, p) for m, p, _ in seen]
    assert posts == [("POST", "/v1/agents"), ("POST", "/v1/agents"), ("POST", "/v1/webhook_endpoints")]
    done, inbox, hook = (b for _, _, b in seen)
    assert done["flow_name"] == "summon_done" and inbox["flow_name"] == "summon_inbox"
    assert done["language"] == "en" and "flow_json" in done and done["flow_json"]["nodes"]
    assert hook["events"] == ["call.completed", "call.analyzed", "call.failed"]
    assert hook["url"].startswith("https://")
    assert out["webhook"]["signing_secret"] == "whsec_x"
    assert out["agent_done"]["id"] == "agent_summon_done"


async def test_place_call_payload_respects_limits(fake_provider):
    seen, transport = fake_provider
    v = ShravanVoice(transport=transport)
    script = CallScript("Satvik", "Claude Code", "demo-api", "Auth refactor",
                        ["x" * 400, "y" * 50], ["Keep the old route?"])
    placed = await v.place_call("call_local_1", "+919999900000", script)
    assert placed.provider_call_id == "call_prov_1"
    _, _, body = seen[-1]
    assert body["agent_id"] == "agent_x" and body["to"] == "+919999900000"
    assert body["contact_name"] == "Satvik"
    assert len(body["flow_vars"]) <= 20
    assert all(len(val) <= 200 for val in body["flow_vars"].values())
    assert body["metadata"]["summon_call_id"] == "call_local_1"
    assert body["sandbox_scenario"] == "completed", "test keys must use the sandbox"
    assert await v.fetch_result("call_prov_1") is None, "not finished yet -> None"
