"""`summon` command line: serve the API, set up the voice provider, answer mock calls,
seed demo memory, and act as the Stop hook for coding agents."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import typer

from summon.config import get_settings

# Hooks and agents pipe UTF-8 JSON through us on Windows consoles that default to cp1252.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Summon AI: your coding agents call you, and remember.")


def _api() -> tuple[str, dict[str, str]]:
    s = get_settings()
    return s.summon_api_url.rstrip("/"), {"Authorization": f"Bearer {s.summon_api_token}"}


# --------------------------------------------------------------------------- serve

@app.command()
def serve(host: str = "0.0.0.0", port: int | None = None, reload: bool = False) -> None:
    """Run the Summon API (and the Memory Lens UI)."""
    import uvicorn

    uvicorn.run("summon.api.app:app", host=host, port=port or get_settings().summon_api_port,
                reload=reload, workers=1)


# --------------------------------------------------------------------------- setup

@app.command()
def setup(webhook_url: str | None = None) -> None:
    """Create the Summon voice agents on the provider and register the results webhook.
    Prints the env values to store (agent id, webhook secret)."""
    s = get_settings()
    if s.summon_voice == "mock":
        typer.echo("SUMMON_VOICE=mock needs no setup. Set sandbox|shravan + SHRAVAN_API_KEY first.")
        raise typer.Exit(1)
    from summon.voice.shravan import ShravanVoice

    url = webhook_url or f"{s.summon_api_url.rstrip('/')}/api/webhooks/shravan"
    if not url.startswith("https://"):
        typer.echo(f"webhook url must be public https, got {url}")
        raise typer.Exit(1)
    out = asyncio.run(ShravanVoice().setup(url))
    typer.echo("Add these to your .env / Coolify environment:")
    typer.echo(f"SHRAVAN_AGENT_ID={out['agent_done']['id']}")
    typer.echo(f"SHRAVAN_WEBHOOK_SECRET={out['webhook'].get('signing_secret', '')}")
    typer.echo(f"# inbox flow (set as the business's inbound flow in the console): "
               f"{out['agent_inbox'].get('flow_id')}")


# --------------------------------------------------------------------------- mock answering

@app.command()
def pending() -> None:
    """List mock calls waiting for a reply."""
    base, _ = _api()
    r = httpx.get(f"{base}/api/mock/pending", timeout=10)
    for c in r.json()["pending"]:
        typer.echo(f"call {c['call_id']}")
        for node, line in c["lines"]:
            typer.echo(f"   🤖 [{node}] {line}")


@app.command()
def answer(call_id: str | None = typer.Argument(None),
           answers: list[str] = typer.Option([], "--a", help="answer per question, in order"),
           more: str = typer.Option("", "--more", help="instruction for the agent")) -> None:
    """Reply to a pending mock call (the phone call, typed)."""
    base, _ = _api()
    if not call_id:
        p = httpx.get(f"{base}/api/mock/pending", timeout=10).json()["pending"]
        if not p:
            typer.echo("no pending mock call")
            raise typer.Exit(1)
        call_id = p[0]["call_id"]
    r = httpx.post(f"{base}/api/mock/reply",
                   json={"call_id": call_id, "answers": answers, "more": more}, timeout=60)
    r.raise_for_status()
    v = r.json()
    typer.echo(f"call {call_id} analyzed: {len(v.get('answers', []))} answers, "
               f"{len(v.get('instructions', []))} instructions, "
               f"{len(v.get('decisions', []))} decisions, {len(v.get('preferences', []))} preferences")


# --------------------------------------------------------------------------- hooks

@app.command()
def hook(agent: str = typer.Argument(..., help="claude | codex")) -> None:
    """Stop-hook entry point. Reads the hook JSON on stdin, calls the developer, and prints
    the hook decision so the agent continues with what was said on the phone."""
    from summon.hooks.stop import run_stop_hook

    # A hook must never crash the agent: bad or empty input means "do nothing".
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    if not payload:
        return
    try:
        result = run_stop_hook(agent, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"summon hook error (ignored): {exc}", file=sys.stderr)
        return
    if result is not None:
        typer.echo(json.dumps(result))


# --------------------------------------------------------------------------- init

@app.command()
def init(path: str = typer.Argument(".", help="project directory to wire up")) -> None:
    """Wire a project to Summon: Claude Code Stop hook + MCP server, Codex hook, Cursor
    MCP; and ~/.summon/.env so hooks can find the API from any directory."""
    import shutil
    from pathlib import Path

    from summon.config import GLOBAL_ENV

    s = get_settings()
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    exe_dir = Path(sys.executable).parent          # the venv's Scripts/bin
    summon_exe = _find_exe(exe_dir, "summon")
    mcp_exe = _find_exe(exe_dir, "summon-mcp")
    if not summon_exe or not mcp_exe:
        summon_exe, mcp_exe = shutil.which("summon") or "summon", shutil.which("summon-mcp") or "summon-mcp"

    GLOBAL_ENV.parent.mkdir(exist_ok=True)
    GLOBAL_ENV.write_text(
        f"SUMMON_API_URL={s.summon_api_url}\nSUMMON_API_TOKEN={s.summon_api_token}\n", "utf-8")

    def write(rel: str, data: dict) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
        typer.echo(f"  wrote {p}")

    hook = lambda agent: {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": f'"{summon_exe}" hook {agent}', "timeout": 900}]}]}}
    mcp = lambda agent: {"mcpServers": {"summon": {
        "type": "stdio", "command": str(mcp_exe), "args": [],
        "env": {"SUMMON_AGENT": agent, "SUMMON_PROJECT": root.name}, "timeout": 900000}}}

    typer.echo(f"Wiring {root} (project id: {root.name})")
    claude_settings = hook("claude")
    # Approve the project MCP server and its tools up front, so `claude -p` runs (and
    # the inbox daemon) can read Summon memory without an interactive prompt.
    claude_settings["enableAllProjectMcpServers"] = True
    claude_settings["permissions"] = {"allow": ["mcp__summon"]}
    write(".claude/settings.json", claude_settings)
    write(".mcp.json", mcp("claude-code"))
    write(".codex/hooks.json", hook("codex"))
    write(".cursor/mcp.json", mcp("cursor"))
    typer.echo("\nCodex MCP is user-level; add this to ~/.codex/config.toml:\n")
    typer.echo("[mcp_servers.summon]\n"
               f'command = "{str(mcp_exe).replace(chr(92), "/")}"\nargs = []\n'
               "startup_timeout_sec = 20\ntool_timeout_sec = 900\n"
               "[mcp_servers.summon.env]\n"
               f'SUMMON_AGENT = "codex"\nSUMMON_PROJECT = "{root.name}"\n')
    typer.echo("Then in Codex run /hooks once to trust .codex/hooks.json.")


def _find_exe(d, name: str):
    for cand in (d / f"{name}.exe", d / name):
        if cand.exists():
            return cand
    return None


# --------------------------------------------------------------------------- demo seed

@app.command("demo-seed")
def demo_seed(project: str = "demo-api") -> None:
    """Seed yesterday's memory so the first demo call already has history."""
    from summon.demo import seed

    seed(project)
    typer.echo(f"seeded demo memory for project {project}")


@app.command()
def brief(project: str, agent: str = "cli") -> None:
    """Print what an agent would read before starting on a project."""
    base, _ = _api()
    r = httpx.get(f"{base}/api/memory/brief", params={"project": project, "agent": agent},
                  timeout=20)
    typer.echo(json.dumps(r.json(), indent=2, ensure_ascii=False))


@app.command()
def daemon(project: str, path: str = ".", agent: str = "claude", every: int = 5) -> None:
    """Watch the inbox for spoken instructions and run them with a coding agent."""
    from summon.daemon import run

    run(project=project, path=os.path.abspath(path), agent=agent, every=every)


if __name__ == "__main__":
    app()
