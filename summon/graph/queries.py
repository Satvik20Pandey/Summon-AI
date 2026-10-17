"""The queries the product depends on. Each one is what an agent (or the UI) reads;
the Cypher is deliberately plain so it can be pasted into FalkorDB Browser."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from falkordb import Graph, Node

from summon.graph.memory import mark_read, now_ts, record_call_decision

# --------------------------------------------------------------------------- helpers

_KEY_PROPS = ("id", "path", "name")


def node_key(n: Node) -> str:
    for k in _KEY_PROPS:
        v = n.properties.get(k)
        if v:
            return str(v)
    return f"_{n.id}"


def to_dict(n: Node | None) -> dict[str, Any] | None:
    if n is None:
        return None
    d = {"key": node_key(n), "label": n.labels[0] if n.labels else "Node"}
    for k, v in n.properties.items():
        if k in ("transcript", "script", "inputs") and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        d[k] = v
    return d


# --------------------------------------------------------------------------- 1. brief

def brief(g: Graph, project_id: str, agent: str | None = None) -> dict[str, Any]:
    """What any agent should know before touching this project. One read, five facets."""
    tasks = g.ro_query(
        """
        MATCH (t:Task)-[:IN]->(s:Session)-[:ON]->(p:Project {id: $pid})
        OPTIONAL MATCH (s)-[:RUN_BY]->(a:Agent)
        RETURN t, a.name
        ORDER BY t.done_ts DESC LIMIT 3
        """,
        {"pid": project_id},
    ).result_set
    open_questions = g.ro_query(
        """
        MATCH (t:Task)-[:IN]->(:Session)-[:ON]->(p:Project {id: $pid}),
              (t)-[:RAISED]->(q:Question)
        WHERE NOT (q)<-[:TO]-(:Answer)
        RETURN q, t.title
        ORDER BY q.asked_ts DESC LIMIT 10
        """,
        {"pid": project_id},
    ).result_set
    decisions = g.ro_query(
        """
        MATCH (d:Decision)-[:APPLIES_TO]->(p:Project {id: $pid})
        WHERE d.until_ts IS NULL
        OPTIONAL MATCH (d)-[:MADE_IN]->(c:Call)
        RETURN d, c.id, c.at
        ORDER BY d.since_ts DESC LIMIT 20
        """,
        {"pid": project_id},
    ).result_set
    prefs = g.ro_query(
        """
        MATCH (pr:Preference)-[:OF]->(:Developer)
        WHERE pr.until_ts IS NULL
        OPTIONAL MATCH (pr)-[:LEARNED_IN]->(c:Call)
        RETURN pr, c.id, c.at
        ORDER BY pr.since_ts DESC LIMIT 20
        """,
    ).result_set
    pending = g.ro_query(
        """
        MATCH (i:Instruction)-[:FOR]->(p:Project {id: $pid})
        WHERE i.status = 'pending'
        OPTIONAL MATCH (i)-[:GIVEN_IN]->(c:Call)
        RETURN i, c.id, c.at
        ORDER BY i.ts ASC LIMIT 10
        """,
        {"pid": project_id},
    ).result_set
    answered = g.ro_query(
        """
        MATCH (t:Task)-[:IN]->(:Session)-[:ON]->(:Project {id: $pid}),
              (t)-[:RAISED]->(q:Question)<-[:TO]-(a:Answer)-[:SAID_IN]->(c:Call)
        RETURN q.text, a.text, c.id, c.at, t.title
        ORDER BY a.ts DESC LIMIT 10
        """,
        {"pid": project_id},
    ).result_set
    history = g.ro_query(
        """
        MATCH (i:Instruction)-[:FOR]->(:Project {id: $pid}), (i)-[:GIVEN_IN]->(c:Call)
        WHERE i.status <> 'pending'
        OPTIONAL MATCH (i)-[:EXECUTED_AS]->(t:Task)
        RETURN i, c.id, c.at, t.title
        ORDER BY i.ts DESC LIMIT 10
        """,
        {"pid": project_id},
    ).result_set

    out = {
        "project": project_id,
        "recent_tasks": [{**to_dict(t), "by_agent": a} for t, a in tasks],
        "open_questions": [{**to_dict(q), "task": title} for q, title in open_questions],
        "decisions": [{**to_dict(d), "call_id": cid, "said_on": at} for d, cid, at in decisions],
        "preferences": [{**to_dict(p), "call_id": cid, "said_on": at} for p, cid, at in prefs],
        "pending_instructions": [{**to_dict(i), "call_id": cid, "said_on": at}
                                 for i, cid, at in pending],
        "answers": [{"question": qt, "answer": at_, "call_id": cid, "said_on": at, "task": title}
                    for qt, at_, cid, at, title in answered],
        "instruction_history": [{**to_dict(i), "call_id": cid, "said_on": at, "executed_as": title}
                                for i, cid, at, title in history],
    }
    if agent:
        ids = [x["id"] for x in out["decisions"] + out["preferences"] + out["recent_tasks"]
               if x.get("id")]
        mark_read(g, agent, ids)
    return out


# --------------------------------------------------------------------------- 2. handoff

def handoff(g: Graph, project_id: str, me: str) -> dict[str, Any]:
    """What other agents did on this project since the start of my previous session,
    and which spoken decisions they produced. Records that I read them."""
    rows = g.ro_query(
        """
        MATCH (s:Session)-[:RUN_BY]->(:Agent {name: $me}), (s)-[:ON]->(:Project {id: $pid})
        RETURN s.started_ts ORDER BY s.started_ts DESC LIMIT 2
        """,
        {"me": me, "pid": project_id},
    ).result_set
    since = rows[-1][0] if len(rows) == 2 else 0

    tasks = g.ro_query(
        """
        MATCH (t:Task)-[:IN]->(s:Session)-[:ON]->(:Project {id: $pid}),
              (s)-[:RUN_BY]->(a:Agent)
        WHERE a.name <> $me AND t.done_ts >= $since
        RETURN t, a.name, s.started_at
        ORDER BY t.done_ts DESC LIMIT 10
        """,
        {"me": me, "pid": project_id, "since": since},
    ).result_set
    decisions = g.ro_query(
        """
        MATCH (d:Decision)-[:APPLIES_TO]->(:Project {id: $pid}),
              (d)-[:MADE_IN]->(c:Call)
        WHERE d.since_ts >= $since AND d.until_ts IS NULL
        OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)-[:IN]->(:Session)-[:RUN_BY]->(a:Agent)
        RETURN d, c.id, c.at, a.name
        ORDER BY d.since_ts DESC LIMIT 20
        """,
        {"pid": project_id, "since": since},
    ).result_set
    instructions = g.ro_query(
        """
        MATCH (i:Instruction)-[:FOR]->(:Project {id: $pid}), (i)-[:GIVEN_IN]->(c:Call)
        WHERE i.ts >= $since
        OPTIONAL MATCH (i)-[:EXECUTED_AS]->(t:Task)
        RETURN i, c.at, t.title
        ORDER BY i.ts DESC LIMIT 10
        """,
        {"pid": project_id, "since": since},
    ).result_set

    out = {
        "me": me,
        "since": datetime.utcfromtimestamp(since).isoformat() + "Z" if since else None,
        "tasks_by_others": [{**to_dict(t), "by_agent": a, "session_started": st}
                            for t, a, st in tasks],
        "decisions": [{**to_dict(d), "call_id": cid, "said_on": at, "while_agent": a}
                      for d, cid, at, a in decisions],
        "instructions": [{**to_dict(i), "said_on": at, "executed_as": title}
                         for i, at, title in instructions],
    }
    mark_read(g, me, [x["id"] for x in out["tasks_by_others"] + out["decisions"] if x.get("id")])
    return out


# --------------------------------------------------------------------------- 3. provenance

def provenance(g: Graph, node_id: str) -> dict[str, Any] | None:
    """Where did this memory come from? The call, when, which task and agent were
    running, who has read it since, and what it replaced."""
    rows = g.ro_query(
        """
        MATCH (n {id: $id})
        OPTIONAL MATCH (n)-[:MADE_IN|GIVEN_IN|LEARNED_IN|SAID_IN]->(c:Call)
        OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)-[:IN]->(s:Session)-[:RUN_BY]->(a:Agent)
        OPTIONAL MATCH (n)-[:SUPERSEDES]->(old)
        RETURN n, c, t, a.name, old
        LIMIT 1
        """,
        {"id": node_id},
    ).result_set
    if not rows:
        return None
    n, c, t, agent, old = rows[0]
    readers = g.ro_query(
        """
        MATCH (r:Agent)-[rd:READ]->(n {id: $id})
        RETURN r.name, rd.at ORDER BY rd.at_ts DESC
        """,
        {"id": node_id},
    ).result_set
    path_rows = g.ro_query(
        """
        MATCH (n {id: $id}), (d:Developer)
        WITH shortestPath((n)-[*..4]->(d)) AS p
        WHERE p IS NOT NULL
        RETURN [x IN nodes(p) | coalesce(x.id, x.path, x.name)] LIMIT 1
        """,
        {"id": node_id},
    ).result_set
    call = to_dict(c)
    if call:
        call.pop("transcript", None)
        call.pop("script", None)
    return {
        "node": to_dict(n),
        "call": call,
        "said_on": c.properties.get("at") if c else None,
        "recording_url": c.properties.get("recording_url") if c else None,
        "during_task": t.properties.get("title") if t else None,
        "while_agent": agent,
        "read_by": [{"agent": r, "at": at} for r, at in readers],
        "supersedes": to_dict(old),
        "path_to_developer": path_rows[0][0] if path_rows else [],
    }


# --------------------------------------------------------------------------- 4. should_call

def should_call(g: Graph, developer_id: str, task_id: str, *, now: int | None = None,
                max_per_hour: int = 6, local_hhmm: str | None = None) -> dict[str, Any]:
    """Track 1: the graph decides. Inputs are read from the graph, the decision is
    written back as a CallDecision node so the UI can show why."""
    now = now or now_ts()
    dev = g.ro_query(
        "MATCH (d:Developer {id: $dev}) RETURN d.quiet_from, d.quiet_to", {"dev": developer_id}
    ).result_set
    quiet_from, quiet_to = (dev[0] if dev else ("", ""))
    pref = g.ro_query(
        """
        MATCH (p:Preference {key: 'quiet_hours'})-[:OF]->(:Developer {id: $dev})
        WHERE p.until_ts IS NULL RETURN p.value LIMIT 1
        """,
        {"dev": developer_id},
    ).result_set
    if pref and pref[0][0] and "-" in pref[0][0]:
        quiet_from, quiet_to = [x.strip() for x in pref[0][0].split("-", 1)]
    recent = g.ro_query(
        """
        MATCH (c:Call)-[:WITH]->(:Developer {id: $dev})
        WHERE c.ts >= $since AND c.direction = 'outbound'
        RETURN count(c), sum(CASE WHEN c.status = 'failed' THEN 1 ELSE 0 END)
        """,
        {"dev": developer_id, "since": now - 3600},
    ).result_set
    calls_last_hour, failed_last_hour = (recent[0] if recent else (0, 0))
    prio = g.ro_query("MATCH (t:Task {id: $tid}) RETURN t.priority", {"tid": task_id}).result_set
    priority = (prio[0][0] if prio and prio[0][0] else "normal")

    hhmm = local_hhmm or datetime.now().strftime("%H:%M")
    in_quiet = _in_window(hhmm, quiet_from, quiet_to)
    inputs = {
        "local_time": hhmm, "quiet_hours": f"{quiet_from}-{quiet_to}" if quiet_from else None,
        "in_quiet_hours": in_quiet, "calls_last_hour": calls_last_hour,
        "failed_last_hour": failed_last_hour, "max_per_hour": max_per_hour,
        "task_priority": priority,
    }
    if calls_last_hour >= max_per_hour:
        action, reason, retry = "skip", f"{calls_last_hour} calls in the last hour (cap {max_per_hour})", 0
    elif in_quiet and priority != "urgent":
        action, reason, retry = "retry_in", f"quiet hours {quiet_from}-{quiet_to} (task is {priority})", _seconds_until(hhmm, quiet_to)
    elif failed_last_hour >= 2:
        action, reason, retry = "retry_in", f"{failed_last_hour} unanswered calls in the last hour", 1800
    else:
        action, reason, retry = "call", "no quiet hours, under the hourly cap, no recent misses", 0
    record_call_decision(g, task_id, action, reason, inputs)
    return {"action": action, "reason": reason, "retry_in_s": retry, "inputs": inputs}


def _in_window(hhmm: str, start: str, end: str) -> bool:
    if not start or not end:
        return False
    t, s, e = _mins(hhmm), _mins(start), _mins(end)
    return (s <= t < e) if s <= e else (t >= s or t < e)


def _seconds_until(hhmm: str, end: str) -> int:
    t, e = _mins(hhmm), _mins(end)
    delta = (e - t) % (24 * 60)
    return max(delta, 1) * 60


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------- 5. inbox + recall

def inbox(g: Graph, project_id: str | None = None) -> list[dict[str, Any]]:
    q = """
        MATCH (i:Instruction)-[:GIVEN_IN]->(c:Call)
        OPTIONAL MATCH (i)-[:FOR]->(p:Project)
        WHERE i.status = 'pending' AND ($pid IS NULL OR p.id = $pid)
        RETURN i, c.at, p.id ORDER BY i.ts ASC LIMIT 20
        """
    rows = g.ro_query(q, {"pid": project_id}).result_set
    return [{**to_dict(i), "said_on": at, "project": pid} for i, at, pid in rows
            if i.properties.get("status") == "pending" and (project_id is None or pid == project_id)]


_WORD = re.compile(r"[A-Za-z0-9]{3,}")


def recall(g: Graph, query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Full-text search across decisions, instructions and task summaries."""
    words = _WORD.findall(query)
    if not words:
        return []
    ft = " | ".join(f"{w}*" if len(w) >= 4 else w for w in words[:8])
    hits: list[tuple[float, dict[str, Any]]] = []
    for label, prop in (("Decision", "text"), ("Instruction", "text"), ("Task", "summary")):
        try:
            rows = g.ro_query(
                f"CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node, score "
                f"RETURN node, score LIMIT {limit}",
                {"q": ft},
            ).result_set
        except Exception:  # noqa: BLE001 - index may not exist yet on an empty graph
            continue
        for node, score in rows:
            d = to_dict(node)
            d["score"] = float(score)
            d["matched"] = prop
            hits.append((float(score), d))
    hits.sort(key=lambda x: -x[0])
    return [d for _, d in hits[:limit]]


# --------------------------------------------------------------------------- 6. timeline (UI)

def timeline(g: Graph, limit: int = 400) -> dict[str, Any]:
    nodes = g.ro_query(
        f"MATCH (n) WHERE NOT n:Event RETURN n LIMIT {limit}"
    ).result_set
    edges = g.ro_query(
        f"MATCH (a)-[r]->(b) WHERE NOT a:Event RETURN a, type(r), b, r LIMIT {limit * 3}"
    ).result_set
    out_nodes = []
    for (n,) in nodes:
        d = to_dict(n)
        d.pop("transcript", None)
        d.pop("script", None)
        out_nodes.append(d)
    out_edges = [{"source": node_key(a), "type": t, "target": node_key(b),
                  **{k: v for k, v in r.properties.items()}} for a, t, b, r in edges]
    return {"nodes": out_nodes, "edges": out_edges}


def stats(g: Graph) -> dict[str, int]:
    rows = g.ro_query(
        """
        MATCH (n) WHERE NOT n:Event
        RETURN labels(n)[0] AS label, count(n) AS c ORDER BY c DESC
        """
    ).result_set
    return {label: int(c) for label, c in rows}
