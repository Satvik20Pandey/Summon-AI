"""Summon API. The graph is the source of truth; this service places calls, receives
their results, runs the curator and serves the Memory Lens."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from summon import __version__
from summon.api import events
from summon.config import get_settings
from summon.curator import condense, extract
from summon.graph import memory as mem
from summon.graph import queries as qry
from summon.graph.schema import get_graph, ping
from summon.voice import mock as mockvoice
from summon.voice.base import CallScript

log = logging.getLogger("summon.api")
WEB = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Summon AI", version=__version__)

# --------------------------------------------------------------------------- plumbing

_backend = None


def backend():
    global _backend
    if _backend is None:
        s = get_settings()
        if s.summon_voice == "mock":
            _backend = mockvoice.MockVoice()
        else:
            from summon.voice.shravan import ShravanVoice

            _backend = ShravanVoice()
    return _backend


def dev_id() -> str:
    return get_settings().summon_developer_id


def graph():
    g = get_graph(dev_id())
    return g


def require_token(authorization: str = Header(default="")) -> None:
    s = get_settings()
    if authorization != f"Bearer {s.summon_api_token}":
        raise HTTPException(401, "bad or missing token")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    s = get_settings()
    g = graph()
    mem.upsert_developer(g, s.summon_developer_id, s.summon_developer_name,
                         s.summon_developer_phone)
    log.info("summon api up: voice=%s developer=%s", s.summon_voice, s.summon_developer_id)
    yield


app.router.lifespan_context = _lifespan


# --------------------------------------------------------------------------- models

class SessionIn(BaseModel):
    agent: str
    project: str
    session_id: str
    model: str = ""
    project_path: str = ""


class CallIn(BaseModel):
    project: str
    agent: str
    session_id: str
    task_id: str
    title: str
    summary: str
    questions: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    priority: str = "normal"
    project_path: str = ""
    instruction_id: str | None = None   # set when this task executes a phoned-in instruction


class MockReplyIn(BaseModel):
    call_id: str
    answers: list[str] = Field(default_factory=list)
    more: str = ""


class RememberIn(BaseModel):
    agent: str
    session_id: str
    project: str
    text: str
    kind: str = "decision"
    topic: str = ""
    applies_to: str = ""


class TaskIn(BaseModel):
    agent: str
    session_id: str
    project: str
    task_id: str
    title: str
    summary: str
    files: list[str] = Field(default_factory=list)
    instruction_id: str | None = None


class InstructionStatusIn(BaseModel):
    status: str
    task_id: str | None = None


# --------------------------------------------------------------------------- health

@app.get("/api/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {"ok": True, "version": __version__, "voice": s.summon_voice, "falkordb": ping(),
            "developer": s.summon_developer_id}


# --------------------------------------------------------------------------- sessions, tasks, notes

@app.post("/api/sessions", dependencies=[Depends(require_token)])
async def start_session(body: SessionIn) -> dict[str, Any]:
    g = graph()
    mem.upsert_project(g, body.project, path=body.project_path)
    mem.start_session(g, body.session_id, body.agent, body.project, body.model)
    events.publish("session.started", agent=body.agent, project=body.project)
    return {"ok": True}


@app.post("/api/tasks", dependencies=[Depends(require_token)])
async def record_task(body: TaskIn) -> dict[str, Any]:
    """A task that did not need a call (e.g. the follow-up after an instruction)."""
    g = graph()
    mem.upsert_project(g, body.project)
    mem.start_session(g, body.session_id, body.agent, body.project)
    mem.record_task(g, body.task_id, body.session_id, body.title, body.summary, files=body.files)
    if body.instruction_id:
        mem.set_instruction_status(g, body.instruction_id, "done", task_id=body.task_id)
    events.publish("task.recorded", agent=body.agent, project=body.project, task_id=body.task_id,
                   title=body.title, instruction_id=body.instruction_id)
    return {"ok": True}


@app.post("/api/memory/remember", dependencies=[Depends(require_token)])
async def remember(body: RememberIn) -> dict[str, Any]:
    g = graph()
    nid = mem.record_agent_note(g, body.agent, body.session_id, body.text, body.kind,
                                body.topic, body.applies_to, body.project)
    events.publish("memory.note", agent=body.agent, project=body.project, id=nid, text=body.text)
    return {"id": nid}


# --------------------------------------------------------------------------- calls

@app.post("/api/calls", dependencies=[Depends(require_token)])
async def place_call(body: CallIn) -> dict[str, Any]:
    """Record the task, let the graph decide whether to call, then call."""
    s = get_settings()
    g = graph()
    mem.upsert_project(g, body.project, path=body.project_path)
    mem.start_session(g, body.session_id, body.agent, body.project)
    chunks, questions = condense(body.summary, body.questions)
    mem.record_task(g, body.task_id, body.session_id, body.title, body.summary,
                    files=body.files, questions=questions, priority=body.priority)
    if body.instruction_id:
        mem.set_instruction_status(g, body.instruction_id, "done", task_id=body.task_id)
        events.publish("instruction.done", instruction_id=body.instruction_id,
                       task_id=body.task_id)

    existing = mem.call_for_task(g, body.task_id)
    if existing:
        call = mem.get_call(g, existing)
        if call and call.get("status") not in ("failed", "skipped"):
            return {"call_id": existing, "action": "existing", "status": call.get("status")}

    decision = qry.should_call(g, s.summon_developer_id, body.task_id,
                               max_per_hour=s.summon_max_calls_per_hour)
    events.publish("call.decision", task_id=body.task_id, **decision)
    call_id = mem.new_id("call")
    script = CallScript(developer_name=s.summon_developer_name, bot=_bot_name(body.agent),
                        project=body.project, task_title=body.title,
                        summary_chunks=chunks, questions=questions)
    if decision["action"] != "call":
        mem.create_call(g, call_id, s.summon_developer_id, "outbound",
                        "skipped" if decision["action"] == "skip" else "deferred",
                        task_id=body.task_id, script=script.to_dict())
        mem.update_call(g, call_id, reason=decision["reason"],
                        retry_in_s=decision["retry_in_s"])
        if decision["action"] == "retry_in":
            asyncio.create_task(_redial_later(call_id, decision["retry_in_s"]))
        return {"call_id": call_id, "action": decision["action"], "reason": decision["reason"],
                "retry_in_s": decision["retry_in_s"], "status": "deferred"}

    mem.create_call(g, call_id, s.summon_developer_id, "outbound", "placing",
                    task_id=body.task_id, script=script.to_dict())
    try:
        placed = await backend().place_call(call_id, s.summon_developer_phone, script)
    except Exception as exc:
        mem.update_call(g, call_id, status="failed", failure_reason=str(exc)[:300])
        events.publish("call.failed", call_id=call_id, reason=str(exc)[:300])
        raise HTTPException(502, f"could not place call: {exc}") from exc
    mem.update_call(g, call_id, status="placed", provider_call_id=placed.provider_call_id)
    events.publish("call.placed", call_id=call_id, task_id=body.task_id, agent=body.agent,
                   project=body.project, lines=script.spoken_lines(),
                   provider=backend().name, provider_call_id=placed.provider_call_id)
    asyncio.create_task(_poll_fallback(call_id, placed.provider_call_id))
    return {"call_id": call_id, "action": "call", "status": "placed",
            "provider": backend().name, "lines": script.spoken_lines()}


def _bot_name(agent: str) -> str:
    return {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor"}.get(agent, agent)


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str, wait: int = Query(0, ge=0, le=900)) -> dict[str, Any]:
    """Long-poll: returns as soon as the call is analyzed/failed/skipped, or after `wait` s.
    State is read from the graph each tick, so a missed event costs at most one tick."""
    deadline = time.time() + wait
    while True:
        g = graph()
        call = mem.get_call(g, call_id)
        if call is None:
            raise HTTPException(404, "unknown call")
        if call.get("status") in ("analyzed", "failed", "skipped") or time.time() >= deadline:
            return _call_view(g, call)
        await events.wait_for_call(call_id, min(15, max(1, deadline - time.time())))


def _call_view(g, call: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in call.items() if k not in ("script",)}
    if isinstance(out.get("transcript"), str):
        try:
            out["transcript"] = json.loads(out["transcript"])
        except ValueError:
            pass
    rows = g.ro_query(
        """
        MATCH (c:Call {id: $cid})
        OPTIONAL MATCH (a:Answer)-[:SAID_IN]->(c), (a)-[:TO]->(q:Question)
        WITH c, collect({question: q.text, answer: a.text}) AS answers
        OPTIONAL MATCH (i:Instruction)-[:GIVEN_IN]->(c)
        WITH c, answers, collect({id: i.id, text: i.text, status: i.status}) AS instructions
        OPTIONAL MATCH (d:Decision)-[:MADE_IN]->(c)
        WITH c, answers, instructions, collect({id: d.id, text: d.text, topic: d.topic}) AS decisions
        OPTIONAL MATCH (p:Preference)-[:LEARNED_IN]->(c)
        RETURN answers, instructions, decisions, collect({key: p.key, value: p.value})
        """,
        {"cid": call["id"]},
    ).result_set
    if rows:
        answers, instructions, decisions, prefs = rows[0]
        out["answers"] = [a for a in answers if a.get("answer")]
        out["instructions"] = [i for i in instructions if i.get("id")]
        out["decisions"] = [d for d in decisions if d.get("id")]
        out["preferences"] = [p for p in prefs if p.get("key")]
    return out


# --------------------------------------------------------------------------- call results

async def finalize_call(call_id: str, resource: dict[str, Any]) -> dict[str, Any]:
    """Common path for webhook, poll fallback and mock reply: curator -> graph -> events."""
    s = get_settings()
    g = graph()
    call = mem.get_call(g, call_id)
    if call is None:
        raise HTTPException(404, "unknown call")
    if call.get("status") == "analyzed":
        return _call_view(g, call)
    if resource.get("status") == "failed":
        reason = resource.get("failure_reason") or "failed"
        mem.update_call(g, call_id, status="failed", failure_reason=reason)
        events.publish("call.failed", call_id=call_id, reason=reason)
        task_id = call.get("task_id")
        if task_id and reason in ("no_answer", "busy"):
            d = qry.should_call(g, s.summon_developer_id, task_id,
                                max_per_hour=s.summon_max_calls_per_hour)
            events.publish("call.decision", task_id=task_id, **d)
            if d["action"] != "skip":
                asyncio.create_task(_redial_later(call_id, d["retry_in_s"] or 300))
        return _call_view(g, mem.get_call(g, call_id))

    transcript = resource.get("transcript") or []
    # Shravan's sandbox answers with a canned transcript: keep it, but label it so the
    # landing page never replays it as if it were a real conversation.
    sandbox = backend().name == "shravan" and resource.get("livemode") is False
    if sandbox:
        mem.update_call(g, call_id, sandbox=True)
    script = call.get("script")
    if isinstance(script, str):
        try:
            script = json.loads(script)
        except ValueError:
            script = {}
    questions = (script or {}).get("questions", [])
    task_id = call.get("task_id")
    qids = [f"{task_id}:q{i}" for i in range(1, len(questions) + 1)] if task_id else []
    project_id = _project_for_call(g, call_id)

    ex = extract(transcript, qids)
    created = mem.record_call_outcome(
        g, call_id, project_id=project_id, transcript=transcript,
        duration_s=resource.get("duration_seconds"), disposition=resource.get("disposition") or "",
        recording_url=resource.get("recording_url") or "",
        answers=ex.answers, instructions=ex.instructions, decisions=ex.decisions,
        preferences=ex.preferences, developer_id=s.summon_developer_id,
    )
    view = _call_view(g, mem.get_call(g, call_id))
    events.publish("call.analyzed", call_id=call_id, task_id=task_id, project=project_id,
                   busy=ex.busy, answers=view.get("answers", []),
                   instructions=view.get("instructions", []), decisions=view.get("decisions", []),
                   preferences=view.get("preferences", []), created=created)
    return view


def _project_for_call(g, call_id: str) -> str | None:
    rows = g.ro_query(
        """
        MATCH (c:Call {id: $cid})-[:ABOUT]->(:Task)-[:IN]->(:Session)-[:ON]->(p:Project)
        RETURN p.id LIMIT 1
        """,
        {"cid": call_id},
    ).result_set
    return rows[0][0] if rows else None


async def _poll_fallback(call_id: str, provider_call_id: str, every: int = 20,
                         max_wait: int = 900) -> None:
    """If no webhook arrives, ask the provider directly."""
    start = time.time()
    while time.time() - start < max_wait:
        await asyncio.sleep(every)
        g = graph()
        call = mem.get_call(g, call_id)
        if not call or call.get("status") in ("analyzed", "failed", "skipped"):
            return
        try:
            res = await backend().fetch_result(provider_call_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("poll failed for %s: %s", call_id, exc)
            continue
        if res:
            log.info("poll fallback resolved %s", call_id)
            await finalize_call(call_id, res)
            return


async def _redial_later(call_id: str, delay_s: int) -> None:
    await asyncio.sleep(max(5, min(delay_s, 3600)))
    s = get_settings()
    g = graph()
    call = mem.get_call(g, call_id)
    if not call or call.get("status") not in ("deferred", "failed"):
        return
    script_raw = call.get("script")
    try:
        sd = json.loads(script_raw) if isinstance(script_raw, str) else (script_raw or {})
    except ValueError:
        return
    if not sd:
        return
    script = CallScript(**{k: sd[k] for k in ("developer_name", "bot", "project", "task_title",
                                               "summary_chunks", "questions")})
    try:
        placed = await backend().place_call(call_id, s.summon_developer_phone, script)
    except Exception as exc:  # noqa: BLE001
        mem.update_call(g, call_id, status="failed", failure_reason=str(exc)[:300])
        return
    mem.update_call(g, call_id, status="placed", provider_call_id=placed.provider_call_id,
                    redialed=True)
    events.publish("call.placed", call_id=call_id, task_id=call.get("task_id"), redial=True,
                   lines=script.spoken_lines(), provider=backend().name)
    asyncio.create_task(_poll_fallback(call_id, placed.provider_call_id))


# --------------------------------------------------------------------------- mock + webhook

@app.get("/api/mock/pending")
async def mock_pending() -> dict[str, Any]:
    return {"pending": [{"call_id": cid, "lines": s.spoken_lines(), "questions": s.questions,
                         "bot": s.bot, "project": s.project, "developer": s.developer_name}
                        for cid, s in mockvoice.PENDING.items()]}


@app.post("/api/mock/reply")
async def mock_reply(body: MockReplyIn) -> dict[str, Any]:
    if get_settings().summon_voice != "mock":
        raise HTTPException(400, "not in mock mode")
    res = mockvoice.reply(body.call_id, body.answers, body.more)
    if res is None:
        raise HTTPException(404, "no pending mock call with that id")
    return await finalize_call(body.call_id, res)


@app.post("/api/webhooks/shravan")
async def shravan_webhook(request: Request) -> JSONResponse:
    from summon.voice.shravan import verify_signature

    s = get_settings()
    raw = await request.body()
    sig = request.headers.get("Shravan-Signature", "")
    if not verify_signature(sig, raw, s.shravan_webhook_secret):
        raise HTTPException(401, "bad signature")
    evt = json.loads(raw)
    g = graph()
    if not mem.record_event(g, evt.get("id", "")):
        return JSONResponse({"ok": True, "duplicate": True})
    kind = evt.get("type", "")
    obj = (evt.get("data") or {}).get("object") or {}
    provider_call_id = obj.get("id", "")
    direction = obj.get("direction", "outbound")

    if direction == "inbound":
        if kind != "call.analyzed":
            return JSONResponse({"ok": True, "ignored": kind})
        await _inbound_call(g, obj)
        return JSONResponse({"ok": True, "inbound": True})

    call_id = mem.call_id_for_provider(g, provider_call_id) or \
        (obj.get("metadata") or {}).get("summon_call_id")
    if not call_id:
        return JSONResponse({"ok": True, "ignored": "unknown call"})
    if kind == "call.failed":
        await finalize_call(call_id, {"status": "failed",
                                      "failure_reason": obj.get("failure_reason") or "failed"})
    elif kind == "call.analyzed":
        await finalize_call(call_id, obj)
    elif kind == "call.completed":
        mem.update_call(g, call_id, status="completed")
        events.publish("call.completed", call_id=call_id)
    return JSONResponse({"ok": True})


async def _inbound_call(g, obj: dict[str, Any]) -> None:
    """The developer phoned in: identify by number, extract instructions, queue them."""
    s = get_settings()
    caller = obj.get("from") or ""
    dev = mem.developer_by_phone(g, caller) if caller else None
    if dev != s.summon_developer_id:
        log.warning("inbound call from unknown number rejected")
        events.publish("call.rejected", reason="unknown caller")
        return
    call_id = mem.new_id("call")
    mem.create_call(g, call_id, dev, "inbound", "completed", provider_call_id=obj.get("id", ""))
    transcript = obj.get("transcript") or []
    ex = extract(transcript, [])
    project = _guess_project(g, transcript)
    mem.record_call_outcome(
        g, call_id, project_id=project, transcript=transcript,
        duration_s=obj.get("duration_seconds"), disposition=obj.get("disposition") or "",
        recording_url=obj.get("recording_url") or "", instructions=ex.instructions,
        decisions=ex.decisions, preferences=ex.preferences, developer_id=dev,
    )
    events.publish("call.inbound", call_id=call_id, project=project,
                   instructions=ex.instructions)


def _guess_project(g, transcript: list[dict[str, Any]]) -> str | None:
    """Match what the caller said against known projects, ignoring -, _ and case."""
    rows = g.ro_query("MATCH (p:Project) RETURN p.id, p.name").result_set
    def norm(x: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()

    text = norm(" ".join(t.get("content", "") for t in transcript if t.get("role") == "user"))
    for pid, name in rows:
        if (norm(name) and norm(name) in text) or norm(pid) in text:
            return pid
    return rows[0][0] if len(rows) == 1 else None


# --------------------------------------------------------------------------- memory reads

@app.get("/api/memory/brief")
async def memory_brief(project: str, agent: str | None = None) -> dict[str, Any]:
    return qry.brief(graph(), project, agent)


@app.get("/api/memory/handoff")
async def memory_handoff(project: str, agent: str) -> dict[str, Any]:
    return qry.handoff(graph(), project, agent)


@app.get("/api/memory/provenance/{node_id}")
async def memory_provenance(node_id: str) -> dict[str, Any]:
    p = qry.provenance(graph(), node_id)
    if p is None:
        raise HTTPException(404, "unknown node")
    return p


@app.get("/api/memory/recall")
async def memory_recall(q: str, agent: str | None = None) -> dict[str, Any]:
    hits = qry.recall(graph(), q)
    if agent:
        mem.mark_read(graph(), agent, [h["id"] for h in hits if h.get("id")])
    return {"query": q, "hits": hits}


@app.get("/api/memory/timeline")
async def memory_timeline() -> dict[str, Any]:
    return qry.timeline(graph())


@app.get("/api/memory/stats")
async def memory_stats() -> dict[str, Any]:
    return {"labels": qry.stats(graph()), "developer": dev_id()}


@app.get("/api/calls")
async def recent_calls(limit: int = Query(5, ge=1, le=50),
                       include_sandbox: bool = False) -> dict[str, Any]:
    """Latest analyzed calls with their transcript and the facts they produced.
    Sandbox (canned) calls are left out unless asked for."""
    g = graph()
    rows = g.ro_query(
        """
        MATCH (c:Call) WHERE c.status = 'analyzed'
          AND ($sb OR coalesce(c.sandbox, false) = false)
        OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)-[:IN]->(:Session)-[:RUN_BY]->(a:Agent)
        OPTIONAL MATCH (t)-[:IN]->(:Session)-[:ON]->(p:Project)
        RETURN c.id, t.title, a.name, p.id
        ORDER BY coalesce(c.analyzed_ts, c.ts) DESC, c.ts DESC LIMIT $n
        """,
        {"n": limit, "sb": include_sandbox},
    ).result_set
    calls = []
    for cid, title, agent, project in rows:
        view = _call_view(g, mem.get_call(g, cid))
        view.update({"task_title": title, "agent": agent, "project": project})
        calls.append(view)
    return {"calls": calls}


@app.get("/api/inbox")
async def inbox(project: str | None = None) -> dict[str, Any]:
    return {"pending": qry.inbox(graph(), project)}


@app.post("/api/instructions/{instruction_id}", dependencies=[Depends(require_token)])
async def instruction_status(instruction_id: str, body: InstructionStatusIn) -> dict[str, Any]:
    mem.set_instruction_status(graph(), instruction_id, body.status, body.task_id)
    events.publish("instruction." + body.status, instruction_id=instruction_id,
                   task_id=body.task_id)
    return {"ok": True}


# --------------------------------------------------------------------------- events + static

@app.get("/api/events")
async def sse() -> EventSourceResponse:
    return EventSourceResponse(events.subscribe())


@app.get("/api/events/recent")
async def recent_events(n: int = 50) -> dict[str, Any]:
    return {"events": events.recent(n)}


if WEB.exists():
    # HTML is revalidated on every load so a deploy is visible immediately; the
    # assets it references carry a ?v= version and can be cached.
    _NO_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html", headers=_NO_CACHE)

    @app.get("/lens")
    async def lens() -> FileResponse:
        return FileResponse(WEB / "lens.html", headers=_NO_CACHE)

    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
