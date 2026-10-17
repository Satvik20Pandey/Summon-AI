"""Shravan.ai as a voice provider, through its public developer API only.

Used exactly as any customer would: create an agent from a flow document, place a
call with per-call variables, and receive signed webhooks. The same client works in
sandbox (``sk_test_``: no dial, canned transcript, real webhooks) and live
(``sk_live_``) modes."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import httpx

from summon.config import get_settings
from summon.voice.base import CallScript, PlacedCall
from summon.voice.flows import summon_done_flow, summon_inbox_flow, validate

log = logging.getLogger(__name__)

EVENTS = ["call.completed", "call.analyzed", "call.failed"]


class ShravanVoice:
    name = "shravan"

    def __init__(self) -> None:
        s = get_settings()
        self.base = s.shravan_base_url.rstrip("/")
        self.key = s.shravan_api_key
        self.agent_id = s.shravan_agent_id
        if not self.key:
            raise RuntimeError("SHRAVAN_API_KEY is required for SUMMON_VOICE=sandbox|shravan")

    @property
    def livemode(self) -> bool:
        return self.key.startswith("sk_live_")

    def _headers(self, idem: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    # ------------------------------------------------------------------ setup

    async def setup(self, webhook_url: str) -> dict[str, Any]:
        """Create (or version) both flows as agents and register the webhook endpoint.
        Returns ids and the signing secret, which the caller stores in its env."""
        out: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=30) as c:
            for key, flow in (("done", summon_done_flow()), ("inbox", summon_inbox_flow())):
                problems = validate(flow)
                if problems:
                    raise RuntimeError(f"flow {key} invalid: {problems}")
                r = await c.post(
                    f"{self.base}/agents", headers=self._headers(),
                    json={"name": f"Summon {key}", "flow_name": f"summon_{key}",
                          "flow_json": flow, "language": "en",
                          "voice": {"tts_provider": "sarvam"}},
                )
                r.raise_for_status()
                out[f"agent_{key}"] = r.json()
            r = await c.post(
                f"{self.base}/webhook_endpoints", headers=self._headers(),
                json={"url": webhook_url, "events": EVENTS,
                      "description": "Summon AI call results"},
            )
            r.raise_for_status()
            out["webhook"] = r.json()
        return out

    # ------------------------------------------------------------------ calls

    async def place_call(self, call_id: str, to_phone: str, script: CallScript) -> PlacedCall:
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
            "to": to_phone,
            "contact_name": script.developer_name,
            "flow_vars": script.flow_vars(),
            "metadata": {"summon_call_id": call_id, "project": script.project[:120]},
        }
        if not self.livemode:
            body["sandbox_scenario"] = "completed"
            body["sandbox_duration_seconds"] = 20
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/calls", headers=self._headers(idem=call_id), json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"provider refused the call: {r.status_code} {r.text[:300]}")
        data = r.json()
        return PlacedCall(provider_call_id=data["id"], status=data.get("status", "placed"),
                          detail={"livemode": data.get("livemode", self.livemode)})

    async def fetch_result(self, provider_call_id: str) -> dict[str, Any] | None:
        """Poll fallback when a webhook has not arrived."""
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{self.base}/calls/{provider_call_id}", headers=self._headers())
            if r.status_code != 200:
                return None
            call = r.json()
            if call.get("status") not in ("completed", "failed"):
                return None
            if call["status"] == "completed" and "transcript" not in call:
                t = await c.get(f"{self.base}/calls/{provider_call_id}/transcript",
                                headers=self._headers())
                call["transcript"] = t.json().get("messages", []) if t.status_code == 200 else []
            return call


# ---------------------------------------------------------------------- webhook signature

def verify_signature(header: str, body: bytes, secret: str, tolerance: int = 300) -> bool:
    """``Shravan-Signature: t=<unix>,v1=<hex>[,v1=<hex>]``; v1 = HMAC-SHA256(secret,
    f"{t}." + raw body). Two v1 values appear during secret rotation."""
    if not header or not secret:
        return False
    parts: dict[str, str] = {}
    v1s: list[str] = []
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        if k == "v1":
            v1s.append(v)
        else:
            parts[k] = v
    try:
        t = int(parts.get("t", "0"))
    except ValueError:
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for v in v1s)


def sign(body: bytes, secret: str, t: int | None = None) -> str:
    """Produce a header the way the provider does (used by tests and the sandbox tool)."""
    t = t or int(time.time())
    v1 = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def new_event_id() -> str:
    return "evt_" + uuid.uuid4().hex[:16]
