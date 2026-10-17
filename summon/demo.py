"""Seed "yesterday's" memory so the first demo call already has history to show:
two earlier tasks, one answered call, a decision and a preference. All synthetic."""

from __future__ import annotations

from summon.config import get_settings
from summon.graph import memory as m
from summon.graph.schema import get_graph


def seed(project: str = "demo-api") -> None:
    s = get_settings()
    g = get_graph(s.summon_developer_id)
    dev = s.summon_developer_id
    m.upsert_developer(g, dev, s.summon_developer_name, s.summon_developer_phone)
    m.upsert_project(g, project, name=project)

    # Day 1: Codex set up the project
    m.start_session(g, "seed-codex-1", "codex", project, model="gpt-5-codex")
    m.record_task(g, "seed-t1", "seed-codex-1", "Scaffold the FastAPI service",
                  "Created the app skeleton with /health and /items, added pytest and a "
                  "Dockerfile.", files=["app/main.py", "tests/test_items.py", "Dockerfile"])

    # Day 2: Claude added auth and phoned the developer
    m.start_session(g, "seed-claude-1", "claude-code", project, model="claude-opus")
    qids = m.record_task(
        g, "seed-t2", "seed-claude-1", "Add JWT authentication",
        "Added /auth/login issuing JWTs, protected /items, wrote 9 tests. The old session "
        "cookie login still exists.",
        files=["app/auth.py", "app/main.py", "tests/test_auth.py"],
        questions=["Should I remove the old session-cookie login?",
                   "Token lifetime: one hour or one day?"],
    )
    m.create_call(g, "seed-call-1", dev, "outbound", "placed", task_id="seed-t2",
                  provider_call_id="seed-provider-1",
                  script={"questions": ["Should I remove the old session-cookie login?",
                                        "Token lifetime: one hour or one day?"]})
    m.record_call_outcome(
        g, "seed-call-1", project_id=project,
        transcript=[
            {"role": "assistant", "content": "Hi, this is Summon calling for Claude Code. "
             "Add JWT authentication on demo-api is done. Should I remove the old "
             "session-cookie login?", "ts": "2026-10-16T14:32:00Z", "node_id": "ask_q1"},
            {"role": "user", "content": "No. Keep the cookie login until the mobile app ships.",
             "ts": "2026-10-16T14:32:20Z", "node_id": "ask_q1"},
            {"role": "assistant", "content": "Token lifetime: one hour or one day?",
             "ts": "2026-10-16T14:32:30Z", "node_id": "ask_q2"},
            {"role": "user", "content": "One hour, with a refresh endpoint.",
             "ts": "2026-10-16T14:32:40Z", "node_id": "ask_q2"},
            {"role": "assistant", "content": "Anything else you want Claude Code to do next?",
             "ts": "2026-10-16T14:32:50Z", "node_id": "ask_more"},
            {"role": "user", "content": "Add the refresh endpoint. And don't call me before 9 am.",
             "ts": "2026-10-16T14:33:05Z", "node_id": "ask_more"},
        ],
        duration_s=68, disposition="information_provided",
        answers=[{"question_id": qids[0], "text": "No. Keep the cookie login until the mobile app ships."},
                 {"question_id": qids[1], "text": "One hour, with a refresh endpoint."}],
        instructions=["Add a /auth/refresh endpoint that issues a new one-hour token."],
        decisions=[{"text": "Keep the session-cookie login until the mobile app ships.",
                    "topic": "cookie-login", "applies_to": "app/auth.py"},
                   {"text": "JWT lifetime is one hour; refresh via /auth/refresh.",
                    "topic": "token-lifetime", "applies_to": "app/auth.py"}],
        preferences=[{"key": "quiet_hours", "value": "22:00-09:00"}],
        developer_id=dev,
    )
    # Claude did the follow-up
    m.record_task(g, "seed-t3", "seed-claude-1", "Add /auth/refresh",
                  "Added /auth/refresh issuing a new one-hour token; 3 tests.",
                  files=["app/auth.py", "tests/test_auth.py"])
    rows = g.ro_query("MATCH (i:Instruction)-[:GIVEN_IN]->(:Call {id:'seed-call-1'}) RETURN i.id").result_set
    for (iid,) in rows:
        m.set_instruction_status(g, iid, "done", task_id="seed-t3")
    # Codex later read the decisions (provenance of consumption)
    m.start_session(g, "seed-codex-2", "codex", project)
    dec = g.ro_query("MATCH (d:Decision) WHERE d.until_ts IS NULL RETURN d.id").result_set
    m.mark_read(g, "codex", [d for (d,) in dec])
