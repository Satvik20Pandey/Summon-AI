"""Regression tests for bugs found in the end-to-end debug pass."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from summon.api import events
from summon.graph import memory as m
from summon.graph import queries as q
from summon.graph.schema import ensure_schema, get_db, ping


async def test_sse_yields_one_data_field_per_event():
    """sse-starlette adds the `data:` prefix itself; a pre-formatted string doubled it
    and the browser could not JSON.parse the Lens timeline."""
    gen = events.subscribe()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    events.publish("unit.test", value=1)
    item = await asyncio.wait_for(task, 2)
    await gen.aclose()
    assert isinstance(item, dict) and set(item) == {"data"}
    assert json.loads(item["data"])["kind"] == "unit.test"


def test_phone_hash_ignores_country_code_and_formatting():
    h = m.phone_hash("+91 98765 43210")
    assert h == m.phone_hash("098765-43210") == m.phone_hash("9876543210")
    assert h != m.phone_hash("9876543211")


def test_quiet_window_crosses_midnight():
    assert q._in_window("23:30", "22:00", "09:00")
    assert q._in_window("08:59", "22:00", "09:00")
    assert not q._in_window("09:00", "22:00", "09:00")
    assert not q._in_window("12:00", "", "")
    assert q._seconds_until("23:00", "09:00") == 10 * 3600


@pytest.mark.skipif(not ping(), reason="no FalkorDB reachable")
def test_timeline_ids_unique_when_labels_share_a_name():
    """A Project and an Agent both called 'probe' must not collide in the Lens."""
    g = get_db().select_graph(f"summon_test_{uuid.uuid4().hex[:8]}")
    ensure_schema(g)
    try:
        m.upsert_developer(g, "dev", "Dev")
        m.start_session(g, "s1", "probe", "probe")
        tl = q.timeline(g)
        uids = [n["uid"] for n in tl["nodes"]]
        assert len(uids) == len(set(uids))
        assert {"Agent:probe", "Project:probe"} <= set(uids)
        eids = [e["id"] for e in tl["edges"]]
        assert len(eids) == len(set(eids))
        node_ids = set(uids)
        assert all(e["source"] in node_ids and e["target"] in node_ids for e in tl["edges"])
    finally:
        g.delete()
