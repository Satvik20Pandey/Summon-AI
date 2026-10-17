"""In-process event bus for the Memory Lens (SSE) and for long-polls.

Best-effort by design: the graph is the source of truth. A client that reconnects
re-reads ``/api/memory/...``; a long-poll that misses an event falls back to the
``Call.status`` it reads from FalkorDB on its next tick."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

_subscribers: set[asyncio.Queue] = set()
_recent: deque[dict[str, Any]] = deque(maxlen=200)
_call_waiters: dict[str, asyncio.Event] = {}


def publish(kind: str, **data: Any) -> None:
    evt = {"kind": kind, "ts": time.time(), **data}
    _recent.append(evt)
    for q in list(_subscribers):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            pass
    call_id = data.get("call_id")
    if call_id and call_id in _call_waiters:
        _call_waiters[call_id].set()


def recent(n: int = 50) -> list[dict[str, Any]]:
    return list(_recent)[-n:]


async def subscribe():
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    try:
        while True:
            evt = await q.get()
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    finally:
        _subscribers.discard(q)


async def wait_for_call(call_id: str, timeout: float) -> bool:
    """Block until an event for this call arrives or the timeout passes."""
    ev = _call_waiters.setdefault(call_id, asyncio.Event())
    try:
        await asyncio.wait_for(ev.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        ev.clear()
