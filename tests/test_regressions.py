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


def test_hook_never_crashes_on_bad_input():
    from typer.testing import CliRunner

    from summon.cli import app

    runner = CliRunner()
    for stdin in ("", "   ", "not json", "[]"):
        r = runner.invoke(app, ["hook", "claude"], input=stdin)
        assert r.exit_code == 0, (stdin, r.output)
        assert r.output.strip() == ""


def test_answer_becomes_decision_with_its_question():
    from summon.curator import extract
    from summon.voice.base import CallScript
    from summon.voice.mock import build_transcript

    s = CallScript("Satvik", "Claude Code", "demo", "t", ["done."],
                   ["Should /version also include the git commit hash?"])
    ex = extract(build_transcript(s, ["Yes, the short hash."], ""), ["t:q1"])
    assert ex.decisions[0]["text"] == ("Should /version also include the git commit hash? "
                                        "-> Yes, the short hash.")
    assert ex.decisions[0]["topic"] == "/version-include-git-commit"


def test_cursor_stop_hook_dialect(monkeypatch):
    from summon.hooks import stop

    seen = {}

    def fake(agent, payload):
        seen.update(agent=agent, payload=payload)
        return {"decision": "block", "reason": "Do the next thing."}

    monkeypatch.setattr(stop, "run_stop_hook", fake)
    out = stop.run_cursor_stop_hook({"status": "completed", "loop_count": 0,
                                     "conversation_id": "c1", "workspace_roots": ["/w/proj"]})
    assert out == {"followup_message": "Do the next thing."}
    assert seen["agent"] == "cursor" and seen["payload"]["cwd"] == "/w/proj"
    assert seen["payload"]["session_id"] == "c1" and not seen["payload"]["stop_hook_active"]
    stop.run_cursor_stop_hook({"status": "completed", "loop_count": 1, "conversation_id": "c1"})
    assert seen["payload"]["stop_hook_active"] is True
    assert stop.run_cursor_stop_hook({"status": "aborted"}) is None


def test_title_keeps_identifiers():
    from summon.hooks.stop import _title

    assert _title("All done — 3 tests passed.") == "3 tests passed."
    assert _title("Fixed summon_recall. More text here.") == "Fixed summon_recall."


def test_title_skips_note_lines():
    from summon.hooks.stop import _title

    msg = "Note: the summon_recall call required a permission.\n\nAdded /items/search. All 9 tests pass."
    assert _title(msg) == "Added /items/search."
    assert _title("One note: nothing else.\n") == "nothing else."
