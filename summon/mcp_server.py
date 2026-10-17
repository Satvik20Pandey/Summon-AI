"""MCP server: the same four tools for Claude Code, Codex and Cursor.

    summon_recall   what is known about this project (brief + what other agents did)
    summon_remember store a decision/note with provenance (this agent, this session)
    summon_done     report a finished task; Summon phones the developer and returns
                    what they said (blocks until the call is analyzed)
    summon_inbox    instructions the developer phoned in that nobody has picked up

Which agent is talking is set by SUMMON_AGENT in the client's MCP config."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import httpx
from mcp.server import MCPServer

from summon.config import get_settings

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer("summon")

AGENT = os.environ.get("SUMMON_AGENT", "unknown-agent")
SESSION_ID = f"{AGENT}-{uuid.uuid4().hex[:10]}"
_session_started: set[str] = set()


def _base() -> tuple[str, dict[str, str]]:
    s = get_settings()
    return s.summon_api_url.rstrip("/"), {"Authorization": f"Bearer {s.summon_api_token}"}


def _project(project: str) -> str:
    return project or os.environ.get("SUMMON_PROJECT") or Path.cwd().name


async def _ensure_session(client: httpx.AsyncClient, base: str, headers: dict, project: str) -> None:
    if project in _session_started:
        return
    await client.post(f"{base}/api/sessions", headers=headers, json={
        "agent": AGENT, "project": project, "session_id": SESSION_ID,
        "project_path": str(Path.cwd())})
    _session_started.add(project)


@mcp.tool()
async def summon_recall(project: str = "", query: str = "") -> str:
    """Read the shared memory for this project before you start: recent tasks by any agent,
    current decisions the developer made on the phone (with the call they came from),
    preferences, open questions, pending instructions, and what other agents did since
    your last session. Optional query runs a search over decisions and instructions."""
    base, headers = _base()
    project = _project(project)
    async with httpx.AsyncClient(timeout=30) as c:
        await _ensure_session(c, base, headers, project)
        brief = (await c.get(f"{base}/api/memory/brief",
                             params={"project": project, "agent": AGENT})).json()
        hand = (await c.get(f"{base}/api/memory/handoff",
                            params={"project": project, "agent": AGENT})).json()
        hits = []
        if query:
            hits = (await c.get(f"{base}/api/memory/recall",
                                params={"q": query, "agent": AGENT})).json().get("hits", [])
    out = [f"# Summon memory for `{project}` (read by {AGENT})"]
    if hand.get("tasks_by_others"):
        out.append("\n## Since your last session, other agents did")
        for t in hand["tasks_by_others"]:
            out.append(f"- [{t['by_agent']}] {t['title']}: {t.get('summary','')[:300]}")
    out.append("\n## Decisions in force (the developer said these on the phone)")
    for d in brief.get("decisions", []):
        src = f" — said on call {d.get('call_id')} at {d.get('said_on')}" if d.get("call_id") else " — noted by an agent"
        out.append(f"- {d['text']}{src} (id {d['id']})")
    if not brief.get("decisions"):
        out.append("- none yet")
    if brief.get("answers"):
        out.append("\n## Answers the developer gave on calls")
        out.extend(f"- Q: {a['question']}  A: {a['answer']} (call {a['call_id']} at {a['said_on']})"
                   for a in brief["answers"])
    if brief.get("instruction_history"):
        out.append("\n## Instructions phoned in earlier")
        out.extend(f"- [{i.get('status')}] {i['text']} (call {i['call_id']} at {i['said_on']})"
                   + (f" -> done as: {i['executed_as']}" if i.get("executed_as") else "")
                   for i in brief["instruction_history"])
    if brief.get("preferences"):
        out.append("\n## Developer preferences")
        out.extend(f"- {p['key']}: {p['value']}" for p in brief["preferences"])
    if brief.get("pending_instructions"):
        out.append("\n## Pending instructions phoned in")
        out.extend(f"- {i['text']} (id {i['id']})" for i in brief["pending_instructions"])
    if brief.get("open_questions"):
        out.append("\n## Open questions nobody has answered")
        out.extend(f"- {q['text']} (task: {q.get('task')})" for q in brief["open_questions"])
    if brief.get("recent_tasks"):
        out.append("\n## Recent tasks")
        out.extend(f"- [{t.get('by_agent')}] {t['title']} ({t.get('done_at','')[:16]})"
                   for t in brief["recent_tasks"])
    if hits:
        out.append(f"\n## Search: {query}")
        out.extend(f"- ({h['label']}) {h.get('text') or h.get('summary','')}" for h in hits)
    return "\n".join(out)


@mcp.tool()
async def summon_remember(text: str, kind: str = "decision", topic: str = "",
                          applies_to: str = "", project: str = "") -> str:
    """Store something future agents must know (a decision, convention or gotcha). It is
    attributed to you and this session, and supersedes an earlier note on the same topic."""
    base, headers = _base()
    project = _project(project)
    async with httpx.AsyncClient(timeout=30) as c:
        await _ensure_session(c, base, headers, project)
        r = await c.post(f"{base}/api/memory/remember", headers=headers, json={
            "agent": AGENT, "session_id": SESSION_ID, "project": project, "text": text,
            "kind": kind, "topic": topic, "applies_to": applies_to})
    return f"remembered as {r.json().get('id')}"


@mcp.tool()
async def summon_done(title: str, summary: str, questions: list[str] | None = None,
                      project: str = "", files: list[str] | None = None) -> str:
    """Call this when a task is finished. Summon phones the developer, reads the summary,
    asks your questions (max 3) and returns their answers and any new instructions.
    Blocks until the call is over (up to ~10 minutes). The graph may decide not to call
    (quiet hours, too many calls); then you get the reason instead."""
    base, headers = _base()
    project = _project(project)
    task_id = "task-" + uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(timeout=120) as c:
        await _ensure_session(c, base, headers, project)
        r = await c.post(f"{base}/api/calls", headers=headers, json={
            "project": project, "agent": AGENT, "session_id": SESSION_ID, "task_id": task_id,
            "title": title, "summary": summary, "questions": questions or [],
            "files": files or []})
        if r.status_code != 200:
            return f"Summon could not place the call: {r.text[:300]}"
        placed = r.json()
        if placed.get("action") not in ("call", "existing"):
            return (f"Summon decided not to call now ({placed.get('action')}): "
                    f"{placed.get('reason')}. Retry in {placed.get('retry_in_s')} s.")
        call_id = placed["call_id"]
        for _ in range(6):
            v = (await c.get(f"{base}/api/calls/{call_id}", params={"wait": 100},
                             timeout=130)).json()
            if v.get("status") in ("analyzed", "failed", "skipped"):
                break
    if v.get("status") != "analyzed":
        return f"Call {call_id} ended with status {v.get('status')}: {v.get('failure_reason','')}"
    lines = [f"The developer answered (call {call_id}, {v.get('at')})."]
    for a in v.get("answers", []):
        lines.append(f"- Q: {a['question']}  A: {a['answer']}")
    if v.get("decisions"):
        lines.append("Decisions to respect from now on:")
        lines.extend(f"- {d['text']}" for d in v["decisions"])
    if v.get("instructions"):
        lines.append("They asked you to do next:")
        lines.extend(f"- {i['text']}" for i in v["instructions"])
    if v.get("preferences"):
        lines.append("Preferences learned: " + ", ".join(f"{p['key']}={p['value']}"
                                                         for p in v["preferences"]))
    return "\n".join(lines)


@mcp.tool()
async def summon_inbox(project: str = "") -> str:
    """Instructions the developer phoned in that are still pending for this project."""
    base, _ = _base()
    project = _project(project)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{base}/api/inbox", params={"project": project})
    items = r.json().get("pending", [])
    if not items:
        return "inbox empty"
    return "\n".join(f"- {i['text']} (id {i['id']}, said on {i.get('said_on')})" for i in items)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
