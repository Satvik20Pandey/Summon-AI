"""Mock voice backend: the "phone call" is a prompt in the Memory Lens UI or the
``summon answer`` CLI. Lets anyone run the full loop with no telephony account.

The reply is turned into a transcript shaped exactly like the real provider's
(``{role, content, ts, node_id}``) so the curator and graph code paths are identical."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from summon.voice.base import CallScript, PlacedCall

# call_id -> script (pending mock calls waiting for a human reply)
PENDING: dict[str, CallScript] = {}


class MockVoice:
    name = "mock"

    async def place_call(self, call_id: str, to_phone: str, script: CallScript) -> PlacedCall:
        PENDING[call_id] = script
        return PlacedCall(provider_call_id=f"mock-{call_id}", status="simulated",
                          detail={"lines": script.spoken_lines()})

    async def fetch_result(self, provider_call_id: str) -> dict[str, Any] | None:
        return None  # mock results only arrive through reply()


def build_transcript(script: CallScript, answers: list[str], more: str) -> list[dict[str, Any]]:
    """Interleave the agent's lines with the human's typed replies."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t: list[dict[str, Any]] = []
    lines = script.spoken_lines()
    t.append({"role": "assistant", "content": lines[0][1], "ts": now, "node_id": "greet"})
    t.append({"role": "user", "content": "Yes, go ahead.", "ts": now, "node_id": "greet"})
    qi = 0
    for node_id, text in lines[1:]:
        t.append({"role": "assistant", "content": text, "ts": now, "node_id": node_id})
        if node_id.startswith("ask_q"):
            reply = answers[qi] if qi < len(answers) else "Skip."
            qi += 1
        else:
            reply = more or "No, that's all."
        t.append({"role": "user", "content": reply, "ts": now, "node_id": node_id})
    t.append({"role": "assistant", "content": "Got it. I'll pass that on. Bye!", "ts": now,
              "node_id": "bye"})
    return t


def reply(call_id: str, answers: list[str], more: str) -> dict[str, Any] | None:
    """Resolve a pending mock call into a provider-shaped call resource."""
    script = PENDING.pop(call_id, None)
    if script is None:
        return None
    transcript = build_transcript(script, answers, more)
    return {
        "id": f"mock-{call_id}",
        "status": "completed",
        "direction": script.direction,
        "duration_seconds": 15 * len(transcript),
        "disposition": "information_provided",
        "recording_url": None,
        "transcript": transcript,
        "livemode": False,
    }
