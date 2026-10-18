"""Stop hook shared by Claude Code and Codex.

Both agents run the hook when they finish a turn, pass JSON on stdin
(``session_id``, ``cwd``, ``stop_hook_active``, ``last_assistant_message`` ...) and
accept ``{"decision": "block", "reason": "..."}`` on stdout, which makes the agent
continue with ``reason`` as its next instruction. That is how what you say on the
phone becomes the agent's next prompt.

Flow:
  1. ``stop_hook_active`` is true  -> this stop ends the work the developer asked for on
     the phone; record it as a Task linked to the Instruction and exit.
  2. Otherwise build the task summary from ``last_assistant_message``, ask the API to
     call (the graph decides), wait for the call, and feed back what was said.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from summon.config import get_settings

log = logging.getLogger("summon.hook")

MIN_MESSAGE_CHARS = 40
HOOK_BUDGET_S = 840          # hooks are configured with timeout 900
_FILE_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|"
                      r"md|json|yaml|yml|toml|html|css|sql|sh))\b")


def _state_path(cwd: str) -> Path:
    p = Path(cwd) / ".summon"
    p.mkdir(exist_ok=True)
    return p / "state.json"


def _load_state(cwd: str) -> dict[str, Any]:
    try:
        return json.loads(_state_path(cwd).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(cwd: str, state: dict[str, Any]) -> None:
    _state_path(cwd).write_text(json.dumps(state, indent=2), "utf-8")


def _log(cwd: str, msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, file=sys.stderr)
    try:
        with open(Path(cwd) / ".summon" / "hook.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _project_id(cwd: str) -> str:
    return os.environ.get("SUMMON_PROJECT") or Path(cwd).name


def _title(message: str) -> str:
    """First sentence of the agent's final message, without markdown or a leading
    "Done —"; underscores stay because they are usually code identifiers."""
    lines = [ln.strip(" #*-") for ln in message.splitlines() if ln.strip()]
    real = [ln for ln in lines if not re.match(r"^(one\s+)?(note|nb|ps|p\.s\.|caveat|fyi)\b", ln, re.IGNORECASE)]
    first = (real or lines or ["Task"])[0]
    first = re.sub(r"[`*]", "", first)
    first = re.sub(r"^(all\s+)?done\s*[—–:\-.,]*\s*", "", first, flags=re.IGNORECASE) or first
    first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
    return (first[:77] + "...") if len(first) > 80 else first


def _questions(message: str) -> list[str]:
    qs = [re.sub(r"^[\s\-*\d.]+", "", ln).strip() for ln in message.splitlines()
          if ln.strip().endswith("?")]
    qs = [re.sub(r"[`*_]", "", q) for q in qs if 10 < len(q) < 300]
    return qs[:3]


def _files(message: str) -> list[str]:
    seen: list[str] = []
    for m in _FILE_RE.findall(message):
        if m not in seen and not m.startswith("http"):
            seen.append(m)
    return seen[:10]


def run_stop_hook(agent_cli: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    agent = {"claude": "claude-code", "codex": "codex", "cursor": "cursor"}.get(agent_cli, agent_cli)
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or "unknown"
    message = (payload.get("last_assistant_message") or "").strip()
    if os.environ.get("SUMMON_DISABLE") == "1":
        return None
    s = get_settings()
    base = s.summon_api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {s.summon_api_token}"}
    project = _project_id(cwd)
    state = _load_state(cwd)

    # ---- 2nd stop: the agent finished what the developer asked for on the phone
    if payload.get("stop_hook_active"):
        pending = state.get("pending_instructions") or []
        if pending:
            task_id = "follow-" + hashlib.sha1((session_id + message).encode()).hexdigest()[:10]
            try:
                httpx.post(f"{base}/api/tasks", headers=headers, timeout=30, json={
                    "agent": agent, "session_id": session_id, "project": project,
                    "task_id": task_id, "title": _title(message) or "Follow-up from call",
                    "summary": message[:4000], "files": _files(message),
                    "instruction_id": pending[0],
                })
                _log(cwd, f"follow-up recorded as {task_id} for instruction {pending[0]}")
            except httpx.HTTPError as exc:
                _log(cwd, f"could not record follow-up: {exc}")
            state["pending_instructions"] = pending[1:]
            _save_state(cwd, state)
        return None

    if len(message) < MIN_MESSAGE_CHARS:
        return None

    task_id = "task-" + hashlib.sha1((session_id + message).encode()).hexdigest()[:10]
    # The daemon leaves the phoned-in instruction this run is executing in state.json.
    executing = (state.get("pending_instructions") or [None])[0]
    body = {
        "project": project, "agent": agent, "session_id": session_id, "task_id": task_id,
        "title": _title(message), "summary": message[:6000], "questions": _questions(message),
        "files": _files(message), "project_path": cwd, "instruction_id": executing,
    }
    if executing:
        state["pending_instructions"] = (state.get("pending_instructions") or [])[1:]
        _save_state(cwd, state)
    try:
        r = httpx.post(f"{base}/api/calls", headers=headers, json=body, timeout=60)
    except httpx.HTTPError as exc:
        _log(cwd, f"summon api unreachable ({exc}); not calling")
        return None
    if r.status_code != 200:
        _log(cwd, f"summon api refused: {r.status_code} {r.text[:200]}")
        return None
    placed = r.json()
    call_id = placed.get("call_id")
    if placed.get("action") != "call" and placed.get("action") != "existing":
        _log(cwd, f"graph decided '{placed.get('action')}': {placed.get('reason')}")
        return _context(agent_cli, f"Summon did not call the developer: {placed.get('reason')}. "
                                   f"It will retry in {placed.get('retry_in_s', 0)} s.")
    _log(cwd, f"📞 calling the developer about {body['title']!r} (call {call_id})")

    # ---- wait for the call to be answered and analyzed
    deadline = time.time() + HOOK_BUDGET_S
    view: dict[str, Any] = {}
    while time.time() < deadline:
        wait = int(min(120, max(5, deadline - time.time())))
        try:
            v = httpx.get(f"{base}/api/calls/{call_id}", params={"wait": wait},
                          timeout=wait + 15).json()
        except (httpx.HTTPError, ValueError) as exc:
            _log(cwd, f"poll error: {exc}")
            time.sleep(5)
            continue
        if v.get("status") in ("analyzed", "failed", "skipped"):
            view = v
            break
    if not view:
        _log(cwd, "call not finished within the hook budget; the result stays in the graph")
        return None
    if view.get("status") != "analyzed":
        _log(cwd, f"call {view.get('status')}: {view.get('failure_reason', '')}")
        return None

    answers = [f"Q: {a['question']} -> A: {a['answer']}" for a in view.get("answers", [])]
    instructions = view.get("instructions", [])
    decisions = [d["text"] for d in view.get("decisions", [])]
    said_on = view.get("at", "")
    _log(cwd, f"call analyzed: {len(answers)} answers, {len(instructions)} instructions, "
              f"{len(decisions)} decisions")

    if instructions:
        for i in instructions:
            try:
                httpx.post(f"{base}/api/instructions/{i['id']}", headers=headers,
                           json={"status": "running"}, timeout=15)
            except httpx.HTTPError:
                pass
        state["pending_instructions"] = [i["id"] for i in instructions]
        state["last_call_id"] = call_id
        _save_state(cwd, state)
        reason = (
            f"The developer answered Summon's phone call (call {call_id}, {said_on}). "
            + (("Their answers: " + " | ".join(answers) + ". ") if answers else "")
            + (("Decisions to respect from now on: " + " | ".join(decisions) + ". ")
               if decisions else "")
            + "They asked you to do this next: "
            + " ".join(f"({n}) {i['text']}" for n, i in enumerate(instructions, 1))
            + " Do it now, then stop. Memory is in Summon (summon_recall) if you need context."
        )
        return {"decision": "block", "reason": reason}

    if answers or decisions:
        return _context(agent_cli, "The developer answered Summon's call. "
                        + " ".join(answers) + (" Decisions: " + "; ".join(decisions) if decisions else ""))
    return None


def run_cursor_stop_hook(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Cursor's ``stop`` hook speaks a different dialect: it sends ``status``,
    ``loop_count``, ``conversation_id`` and ``workspace_roots`` (no final message), and
    continues the agent when the hook returns ``{"followup_message": ...}``."""
    if payload.get("status") not in (None, "completed"):
        return None
    if int(payload.get("loop_count") or 0) > 0:
        # Cursor re-fires stop after a follow-up; treat it like stop_hook_active.
        payload = {**payload, "stop_hook_active": True}
    roots = payload.get("workspace_roots") or []
    normalized = {
        "cwd": payload.get("cwd") or (roots[0] if roots else os.getcwd()),
        "session_id": payload.get("conversation_id") or payload.get("session_id") or "cursor",
        "stop_hook_active": payload.get("stop_hook_active", False),
        "last_assistant_message": payload.get("last_assistant_message")
        or "Cursor finished the task it was working on in this workspace. "
           "Review the changes in the editor.",
    }
    result = run_stop_hook("cursor", normalized)
    if result and result.get("decision") == "block":
        return {"followup_message": result["reason"]}
    return None


def _context(agent_cli: str, text: str) -> dict[str, Any] | None:
    """Non-blocking feedback. Claude Code understands hookSpecificOutput; Codex only
    honours block decisions, so there it is logged and nothing is returned."""
    if agent_cli == "claude":
        return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text}}
    print(text, file=sys.stderr)
    return None
