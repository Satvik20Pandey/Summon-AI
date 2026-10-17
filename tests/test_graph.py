"""Graph layer tests against a live FalkorDB. Skipped when none is reachable.

Run: uv run pytest tests/test_graph.py -q
"""

from __future__ import annotations

import uuid

import pytest

from summon.graph import memory as m
from summon.graph import queries as q
from summon.graph.schema import get_db, ping

pytestmark = pytest.mark.skipif(not ping(), reason="no FalkorDB reachable")


@pytest.fixture
def g():
    name = f"summon_test_{uuid.uuid4().hex[:8]}"
    graph = get_db().select_graph(name)
    from summon.graph.schema import ensure_schema

    ensure_schema(graph)
    yield graph
    graph.delete()


def _seed(g):
    m.upsert_developer(g, "dev1", "Dev One", "+911234567890")
    m.start_session(g, "s_claude_1", "claude-code", "proj", model="opus")
    qids = m.record_task(
        g, "task1", "s_claude_1", "Refactor auth",
        "Moved login to /auth/login and added tests.",
        files=["api/auth.py"], questions=["Keep the old /login route?"],
    )
    m.create_call(g, "call1", "dev1", "outbound", "placed", task_id="task1",
                  provider_call_id="prov-1")
    created = m.record_call_outcome(
        g, "call1", project_id="proj",
        transcript=[{"role": "assistant", "content": "Keep the old route?"},
                    {"role": "user", "content": "Yes, keep it backward compatible."}],
        duration_s=42, disposition="information_provided",
        answers=[{"question_id": qids[0], "text": "Yes"}],
        instructions=["Add a deprecation header to /login"],
        decisions=[{"text": "Keep /login backward compatible", "topic": "api-compat"}],
        preferences=[{"key": "quiet_hours", "value": "22:00-09:00"}],
        developer_id="dev1",
    )
    return created


def test_brief_and_provenance(g):
    created = _seed(g)
    b = q.brief(g, "proj", agent="codex")
    assert b["recent_tasks"][0]["title"] == "Refactor auth"
    assert b["decisions"][0]["text"] == "Keep /login backward compatible"
    assert b["decisions"][0]["call_id"] == "call1"
    assert b["pending_instructions"][0]["text"].startswith("Add a deprecation")
    assert not b["open_questions"], "answered question must not be open"

    p = q.provenance(g, created["decisions"][0])
    assert p["call"]["id"] == "call1"
    assert p["during_task"] == "Refactor auth"
    assert p["while_agent"] == "claude-code"
    assert p["read_by"][0]["agent"] == "codex"
    assert "dev1" in p["path_to_developer"]


def test_handoff_sees_other_agents_work(g):
    _seed(g)
    m.start_session(g, "s_codex_1", "codex", "proj")
    h = q.handoff(g, "proj", "codex")
    assert [t["title"] for t in h["tasks_by_others"]] == ["Refactor auth"]
    assert h["decisions"][0]["while_agent"] == "claude-code"
    assert h["decisions"][0]["said_on"]


def test_supersede_keeps_history(g):
    _seed(g)
    m.create_call(g, "call2", "dev1", "outbound", "placed", task_id="task1")
    new = m.record_decision(g, "call2", "Drop /login after v2 ships", topic="api-compat",
                            project_id="proj")
    b = q.brief(g, "proj")
    assert [d["text"] for d in b["decisions"]] == ["Drop /login after v2 ships"]
    p = q.provenance(g, new)
    assert p["supersedes"]["text"] == "Keep /login backward compatible"


def test_event_idempotency(g):
    assert m.record_event(g, "evt_1") is True
    assert m.record_event(g, "evt_1") is False


def test_should_call_policy(g):
    _seed(g)
    d = q.should_call(g, "dev1", "task1", local_hhmm="23:30")
    assert d["action"] == "retry_in" and "quiet" in d["reason"]
    d = q.should_call(g, "dev1", "task1", local_hhmm="11:00")
    assert d["action"] == "call"
    rows = g.ro_query("MATCH (x:CallDecision)-[:FOR]->(:Task {id:'task1'}) RETURN count(x)").result_set
    assert rows[0][0] == 2


def test_recall_fulltext(g):
    _seed(g)
    hits = q.recall(g, "backward compatible login")
    assert any(h["label"] == "Decision" for h in hits)


def test_one_call_per_task_and_lookup(g):
    _seed(g)
    assert m.call_for_task(g, "task1") == "call1"
    assert m.call_id_for_provider(g, "prov-1") == "call1"
    assert m.developer_by_phone(g, "+91 12345 67890") == "dev1"
