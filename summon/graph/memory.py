"""Writers. Every write is a MERGE on a stable id, so webhook retries and re-fired
hooks never create duplicates. Provenance edges always point at the Call a fact
came from."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from falkordb import Graph


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def iso(ts: int | None = None) -> str:
    return datetime.fromtimestamp(ts or now_ts(), tz=timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def phone_hash(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return hashlib.sha256(("summon:" + digits).encode()).hexdigest()[:24]


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == " " else " " for ch in text)
    words = [w.lower() for w in cleaned.split()]
    return "-".join(words[:4]) or "general"


# --------------------------------------------------------------------------- identity

def upsert_developer(g: Graph, dev_id: str, name: str, phone: str = "",
                     quiet_from: str = "", quiet_to: str = "") -> None:
    g.query(
        """
        MERGE (d:Developer {id: $id})
        SET d.name = $name, d.phone_hash = $ph, d.quiet_from = $qf, d.quiet_to = $qt
        """,
        {"id": dev_id, "name": name, "ph": phone_hash(phone) if phone else "",
         "qf": quiet_from, "qt": quiet_to},
    )


def developer_by_phone(g: Graph, phone: str) -> str | None:
    rows = g.ro_query(
        "MATCH (d:Developer {phone_hash: $ph}) RETURN d.id LIMIT 1", {"ph": phone_hash(phone)}
    ).result_set
    return rows[0][0] if rows else None


def upsert_project(g: Graph, project_id: str, name: str = "", path: str = "") -> None:
    g.query(
        """
        MERGE (p:Project {id: $id})
        ON CREATE SET p.name = $name, p.path = $path, p.created_ts = $ts
        SET p.name = CASE WHEN $name = '' THEN p.name ELSE $name END,
            p.path = CASE WHEN $path = '' THEN p.path ELSE $path END
        """,
        {"id": project_id, "name": name or project_id, "path": path, "ts": now_ts()},
    )


def start_session(g: Graph, session_id: str, agent: str, project_id: str,
                  model: str = "") -> None:
    upsert_project(g, project_id)
    g.query(
        """
        MERGE (a:Agent {name: $agent})
        SET a.model = CASE WHEN $model = '' THEN a.model ELSE $model END
        MERGE (s:Session {id: $sid})
        ON CREATE SET s.started_ts = $ts, s.started_at = $iso
        MERGE (s)-[:RUN_BY]->(a)
        WITH s
        MATCH (p:Project {id: $pid})
        MERGE (s)-[:ON]->(p)
        """,
        {"agent": agent, "model": model, "sid": session_id, "pid": project_id,
         "ts": now_ts(), "iso": iso()},
    )


# --------------------------------------------------------------------------- tasks

def record_task(g: Graph, task_id: str, session_id: str, title: str, summary: str,
                status: str = "done", files: list[str] | None = None,
                questions: list[str] | None = None, priority: str = "normal") -> list[str]:
    """Create/refresh a Task under a session. Returns the Question ids in order."""
    ts = now_ts()
    g.query(
        """
        MATCH (s:Session {id: $sid})
        MERGE (t:Task {id: $tid})
        ON CREATE SET t.started_ts = $ts, t.started_at = $iso
        SET t.title = $title, t.summary = $summary, t.status = $status,
            t.priority = $priority, t.done_ts = $ts, t.done_at = $iso
        MERGE (t)-[:IN]->(s)
        """,
        {"sid": session_id, "tid": task_id, "title": title, "summary": summary,
         "status": status, "priority": priority, "ts": ts, "iso": iso(ts)},
    )
    for path in files or []:
        g.query(
            """
            MATCH (t:Task {id: $tid})
            MERGE (f:File {path: $path})
            MERGE (t)-[:TOUCHED]->(f)
            """,
            {"tid": task_id, "path": path},
        )
    qids: list[str] = []
    for i, q in enumerate(questions or [], start=1):
        qid = f"{task_id}:q{i}"
        qids.append(qid)
        g.query(
            """
            MATCH (t:Task {id: $tid})
            MERGE (q:Question {id: $qid})
            SET q.text = $text, q.asked_ts = $ts
            MERGE (t)-[:RAISED]->(q)
            """,
            {"tid": task_id, "qid": qid, "text": q, "ts": ts},
        )
    return qids


# --------------------------------------------------------------------------- calls

def create_call(g: Graph, call_id: str, developer_id: str, direction: str,
                status: str, task_id: str | None = None,
                provider_call_id: str = "", script: dict[str, Any] | None = None) -> None:
    ts = now_ts()
    g.query(
        """
        MATCH (d:Developer {id: $dev})
        MERGE (c:Call {id: $cid})
        ON CREATE SET c.ts = $ts, c.at = $iso, c.direction = $dir
        SET c.status = $status,
            c.provider_call_id = CASE WHEN $pcid = '' THEN c.provider_call_id ELSE $pcid END,
            c.script = CASE WHEN $script = '' THEN c.script ELSE $script END
        MERGE (c)-[:WITH]->(d)
        """,
        {"dev": developer_id, "cid": call_id, "ts": ts, "iso": iso(ts), "dir": direction,
         "status": status, "pcid": provider_call_id,
         "script": _json(script) if script else ""},
    )
    if task_id:
        g.query(
            """
            MATCH (c:Call {id: $cid}), (t:Task {id: $tid})
            MERGE (c)-[:ABOUT]->(t)
            """,
            {"cid": call_id, "tid": task_id},
        )


def update_call(g: Graph, call_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"c.{k} = ${k}" for k in fields)
    g.query(f"MATCH (c:Call {{id: $cid}}) SET {sets}", {"cid": call_id, **fields})


def get_call(g: Graph, call_id: str) -> dict[str, Any] | None:
    rows = g.ro_query(
        """
        MATCH (c:Call {id: $cid})
        OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)
        RETURN c, t.id
        """,
        {"cid": call_id},
    ).result_set
    if not rows:
        return None
    props = dict(rows[0][0].properties)
    props["task_id"] = rows[0][1]
    return props


def call_for_task(g: Graph, task_id: str) -> str | None:
    rows = g.ro_query(
        "MATCH (c:Call)-[:ABOUT]->(t:Task {id: $tid}) RETURN c.id ORDER BY c.ts DESC LIMIT 1",
        {"tid": task_id},
    ).result_set
    return rows[0][0] if rows else None


def call_id_for_provider(g: Graph, provider_call_id: str) -> str | None:
    rows = g.ro_query(
        "MATCH (c:Call {provider_call_id: $p}) RETURN c.id LIMIT 1", {"p": provider_call_id}
    ).result_set
    return rows[0][0] if rows else None


def record_call_outcome(
    g: Graph,
    call_id: str,
    *,
    project_id: str | None,
    transcript: list[dict[str, Any]],
    duration_s: int | None = None,
    disposition: str = "",
    recording_url: str = "",
    answers: list[dict[str, str]] | None = None,      # {question_id, text}
    instructions: list[str] | None = None,
    decisions: list[dict[str, str]] | None = None,    # {text, topic, applies_to}
    preferences: list[dict[str, str]] | None = None,  # {key, value}
    developer_id: str = "",
) -> dict[str, list[str]]:
    """Persist everything the curator extracted, each fact linked to this Call."""
    ts = now_ts()
    update_call(
        g, call_id,
        status="analyzed", transcript=_json(transcript), duration_s=duration_s or 0,
        disposition=disposition, recording_url=recording_url, analyzed_ts=ts,
    )
    created: dict[str, list[str]] = {"answers": [], "instructions": [], "decisions": [],
                                     "preferences": []}

    for a in answers or []:
        aid = new_id("ans")
        g.query(
            """
            MATCH (c:Call {id: $cid}), (q:Question {id: $qid})
            CREATE (a:Answer {id: $aid, text: $text, ts: $ts})
            CREATE (a)-[:TO]->(q)
            CREATE (a)-[:SAID_IN]->(c)
            """,
            {"cid": call_id, "qid": a["question_id"], "aid": aid, "text": a["text"], "ts": ts},
        )
        created["answers"].append(aid)

    for text in instructions or []:
        iid = new_id("ins")
        g.query(
            """
            MATCH (c:Call {id: $cid})
            CREATE (i:Instruction {id: $iid, text: $text, status: 'pending', ts: $ts, at: $iso})
            CREATE (i)-[:GIVEN_IN]->(c)
            """,
            {"cid": call_id, "iid": iid, "text": text, "ts": ts, "iso": iso(ts)},
        )
        if project_id:
            g.query(
                """
                MATCH (i:Instruction {id: $iid}), (p:Project {id: $pid})
                MERGE (i)-[:FOR]->(p)
                """,
                {"iid": iid, "pid": project_id},
            )
        created["instructions"].append(iid)

    for d in decisions or []:
        did = record_decision(g, call_id, d["text"], d.get("topic", ""),
                              d.get("applies_to", ""), project_id)
        created["decisions"].append(did)

    for p in preferences or []:
        pid_ = record_preference(g, call_id, developer_id, p["key"], p["value"])
        created["preferences"].append(pid_)

    return created


def record_decision(g: Graph, call_id: str, text: str, topic: str = "",
                    applies_to: str = "", project_id: str | None = None) -> str:
    """A new Decision closes the previous current one on the same topic+project
    (``until`` set) and links SUPERSEDES, so history is kept, never overwritten."""
    did = new_id("dec")
    ts = now_ts()
    topic = topic or _slug(text)
    g.query(
        """
        MATCH (c:Call {id: $cid})
        CREATE (d:Decision {id: $did, text: $text, topic: $topic, applies_to: $applies,
                            since_ts: $ts, since: $iso})
        CREATE (d)-[:MADE_IN]->(c)
        """,
        {"cid": call_id, "did": did, "text": text, "topic": topic, "applies": applies_to,
         "ts": ts, "iso": iso(ts)},
    )
    if project_id:
        g.query(
            """
            MATCH (d:Decision {id: $did}), (p:Project {id: $pid})
            MERGE (d)-[:APPLIES_TO]->(p)
            """,
            {"did": did, "pid": project_id},
        )
        g.query(
            """
            MATCH (old:Decision {topic: $topic})-[:APPLIES_TO]->(p:Project {id: $pid})
            WHERE old.id <> $did AND old.until_ts IS NULL
            SET old.until_ts = $ts, old.until = $iso
            WITH old
            MATCH (d:Decision {id: $did})
            MERGE (d)-[:SUPERSEDES]->(old)
            """,
            {"did": did, "pid": project_id, "topic": topic, "ts": ts, "iso": iso(ts)},
        )
    if applies_to:
        g.query(
            """
            MATCH (d:Decision {id: $did})
            MERGE (f:File {path: $path})
            MERGE (d)-[:APPLIES_TO]->(f)
            """,
            {"did": did, "path": applies_to},
        )
    return did


def record_agent_note(g: Graph, agent: str, session_id: str, text: str, kind: str = "decision",
                      topic: str = "", applies_to: str = "", project_id: str | None = None) -> str:
    """A fact an agent chose to remember itself (summon_remember). Provenance is the
    agent + session instead of a call; the same SUPERSEDES rule applies."""
    did = new_id("dec" if kind == "decision" else "note")
    ts = now_ts()
    topic = topic or _slug(text)
    g.query(
        """
        MERGE (a:Agent {name: $agent})
        MERGE (s:Session {id: $sid})
        ON CREATE SET s.started_ts = $ts, s.started_at = $iso
        MERGE (s)-[:RUN_BY]->(a)
        CREATE (d:Decision {id: $did, text: $text, topic: $topic, applies_to: $applies,
                            kind: $kind, source: 'agent', since_ts: $ts, since: $iso})
        CREATE (d)-[:WRITTEN_BY]->(a)
        CREATE (d)-[:IN_SESSION]->(s)
        """,
        {"agent": agent, "sid": session_id, "did": did, "text": text, "topic": topic,
         "applies": applies_to, "kind": kind, "ts": ts, "iso": iso(ts)},
    )
    if project_id:
        upsert_project(g, project_id)
        g.query(
            """
            MATCH (d:Decision {id: $did}), (p:Project {id: $pid})
            MERGE (d)-[:APPLIES_TO]->(p)
            """,
            {"did": did, "pid": project_id},
        )
        g.query(
            """
            MATCH (old:Decision {topic: $topic})-[:APPLIES_TO]->(p:Project {id: $pid})
            WHERE old.id <> $did AND old.until_ts IS NULL
            SET old.until_ts = $ts, old.until = $iso
            WITH old
            MATCH (d:Decision {id: $did})
            MERGE (d)-[:SUPERSEDES]->(old)
            """,
            {"did": did, "pid": project_id, "topic": topic, "ts": ts, "iso": iso(ts)},
        )
    return did


def record_preference(g: Graph, call_id: str, developer_id: str, key: str, value: str) -> str:
    pid = new_id("pref")
    ts = now_ts()
    g.query(
        """
        MATCH (old:Preference {key: $key})-[:OF]->(d:Developer {id: $dev})
        WHERE old.until_ts IS NULL
        SET old.until_ts = $ts
        """,
        {"dev": developer_id, "key": key, "ts": ts},
    )
    g.query(
        """
        MATCH (d:Developer {id: $dev}), (c:Call {id: $cid})
        CREATE (p:Preference {id: $pid, key: $key, value: $value, since_ts: $ts, since: $iso})
        CREATE (p)-[:OF]->(d)
        CREATE (p)-[:LEARNED_IN]->(c)
        """,
        {"dev": developer_id, "key": key, "value": value, "pid": pid, "ts": ts,
         "iso": iso(ts), "cid": call_id},
    )
    return pid


def record_call_decision(g: Graph, task_id: str, action: str, reason: str,
                         inputs: dict[str, Any]) -> str:
    """The graph's own decision about whether to call (Track 1), kept as a node."""
    cid = new_id("cd")
    ts = now_ts()
    g.query(
        """
        MATCH (t:Task {id: $tid})
        CREATE (x:CallDecision {id: $cid, action: $action, reason: $reason,
                                inputs: $inputs, ts: $ts, at: $iso})
        CREATE (x)-[:FOR]->(t)
        """,
        {"tid": task_id, "cid": cid, "action": action, "reason": reason,
         "inputs": _json(inputs), "ts": ts, "iso": iso(ts)},
    )
    return cid


# --------------------------------------------------------------------------- misc

def mark_read(g: Graph, agent: str, node_ids: list[str]) -> None:
    """Provenance of consumption: which agent read which memory, and when."""
    if not node_ids:
        return
    g.query(
        """
        MERGE (a:Agent {name: $agent})
        WITH a
        UNWIND $ids AS nid
        MATCH (n) WHERE n.id = nid AND (n:Decision OR n:Preference OR n:Task OR n:Instruction)
        MERGE (a)-[r:READ]->(n)
        SET r.at_ts = $ts, r.at = $iso
        """,
        {"agent": agent, "ids": node_ids, "ts": now_ts(), "iso": iso()},
    )


def record_event(g: Graph, event_id: str) -> bool:
    """Idempotency gate for webhooks. Returns True only the first time an id is seen."""
    rows = g.query(
        """
        MERGE (e:Event {id: $id})
        ON CREATE SET e.fresh = true, e.ts = $ts
        ON MATCH SET e.fresh = false
        RETURN e.fresh
        """,
        {"id": event_id, "ts": now_ts()},
    ).result_set
    return bool(rows and rows[0][0])


def set_instruction_status(g: Graph, instruction_id: str, status: str,
                           task_id: str | None = None) -> None:
    g.query(
        "MATCH (i:Instruction {id: $iid}) SET i.status = $status, i.updated_ts = $ts",
        {"iid": instruction_id, "status": status, "ts": now_ts()},
    )
    if task_id:
        g.query(
            """
            MATCH (i:Instruction {id: $iid}), (t:Task {id: $tid})
            MERGE (i)-[:EXECUTED_AS]->(t)
            """,
            {"iid": instruction_id, "tid": task_id},
        )
