"""Inbox daemon: turns instructions the developer phoned in into agent runs.

Polls ``/api/inbox`` for pending Instructions on one project, runs each with a coding
agent inside ONE directory you chose (never anywhere else), and relies on the Stop hook
in that directory to phone the developer back when the agent finishes. The
Instruction -> Task link (``EXECUTED_AS``) is written by the hook's follow-up path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from summon.config import get_settings


def _cmd(agent: str, instruction: str) -> list[str]:
    if agent == "codex":
        exe = shutil.which("codex") or "codex"
        return [exe, "exec", "--approve-for-me",
                "--skip-git-repo-check", instruction]
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", instruction, "--permission-mode", "acceptEdits", "--max-turns", "25",
            "--output-format", "text"]


def run(project: str, path: str, agent: str = "claude", every: int = 5) -> None:
    s = get_settings()
    base, headers = s.summon_api_url.rstrip("/"), {"Authorization": f"Bearer {s.summon_api_token}"}
    root = Path(path).resolve()
    if not (root / ".claude" / "settings.json").exists() and not (root / ".codex").exists():
        print(f"{root} is not wired to Summon; run `summon init {root}` first", file=sys.stderr)
        sys.exit(1)
    print(f"summon daemon: project={project} dir={root} agent={agent}; waiting for phoned-in tasks")
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    while True:
        try:
            items = httpx.get(f"{base}/api/inbox", params={"project": project}, timeout=20)\
                .json().get("pending", [])
        except (httpx.HTTPError, ValueError) as exc:
            print(f"inbox poll failed: {exc}", file=sys.stderr)
            time.sleep(every)
            continue
        for it in items:
            iid, text = it["id"], it["text"].strip()
            if not text:
                continue
            httpx.post(f"{base}/api/instructions/{iid}", headers=headers,
                       json={"status": "running"}, timeout=15)
            state = root / ".summon"
            state.mkdir(exist_ok=True)
            (state / "state.json").write_text(json.dumps({"pending_instructions": [iid]}), "utf-8")
            prompt = (f"The developer phoned in this task (Summon instruction {iid}): {text}\n"
                      "Do it. First call summon_recall if available. When done, summarize in 3 "
                      "lines and ask at most one question.")
            print(f"▶ running {agent} for instruction {iid}: {text[:80]}")
            try:
                subprocess.run(_cmd(agent, prompt), cwd=str(root), env=env, timeout=1800,
                               check=False)
            except subprocess.TimeoutExpired:
                print(f"instruction {iid} timed out", file=sys.stderr)
            # the Stop hook recorded the follow-up task and linked it; make sure status moved
            httpx.post(f"{base}/api/instructions/{iid}", headers=headers,
                       json={"status": "done"}, timeout=15)
            print(f"✔ instruction {iid} finished")
        time.sleep(every)
