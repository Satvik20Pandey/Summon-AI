# Wiring an agent to Summon

`summon init <project-dir>` writes all of the files below with absolute paths. This page
shows what they contain so you can hand-edit them.

Every integration needs `~/.summon/.env` (written by `summon init`):

```
SUMMON_API_URL=http://localhost:8787
SUMMON_API_TOKEN=<same value the API runs with>
```

## Claude Code

`.claude/settings.json` — the Stop hook. When Claude stops, Summon phones you; your
instruction comes back as `{"decision":"block","reason":"…"}` and Claude continues with it.

```json
{
  "hooks": {
    "Stop": [{ "hooks": [{ "type": "command", "command": "summon hook claude", "timeout": 900 }] }]
  }
}
```

`.mcp.json` — the memory tools.

```json
{
  "mcpServers": {
    "summon": {
      "type": "stdio", "command": "summon-mcp", "args": [],
      "env": { "SUMMON_AGENT": "claude-code" }, "timeout": 900000
    }
  }
}
```

Non-interactive runs: `claude -p "…" --permission-mode acceptEdits`. To let `-p` runs use
the memory tools without a prompt, add `--allowedTools "mcp__summon__*"`.

## Codex CLI

`.codex/hooks.json` (same contract as Claude Code; trust it once with `/hooks` inside Codex,
or pass `--dangerously-bypass-hook-trust` to `codex exec` in automation):

```json
{
  "hooks": {
    "Stop": [{ "hooks": [{ "type": "command", "command": "summon hook codex", "timeout": 900 }] }]
  }
}
```

MCP is user-level in Codex:

```bash
codex mcp add summon --env SUMMON_AGENT=codex -- summon-mcp
```

and in `~/.codex/config.toml` raise the tool timeout so `summon_done` can wait for a call:

```toml
[mcp_servers.summon]
startup_timeout_sec = 20
tool_timeout_sec = 900
```

Non-interactive: `codex exec --approve-for-me "…"`.

## Cursor

`.cursor/mcp.json`:

```json
{ "mcpServers": { "summon": { "command": "summon-mcp", "args": [], "env": { "SUMMON_AGENT": "cursor" } } } }
```

Cursor's `stop` hook returns `{"followup_message": "…"}` instead of a block decision; a
thin adapter around `summon hook` is the next integration and is not exercised in the demo.

## What the hook sends

`POST /api/calls` with `project` (directory name), `agent`, `session_id`, a stable
`task_id` (hash of session + final message), the agent's final message as `summary`, lines
ending in `?` as `questions` (max 3) and file paths it mentioned. The API condenses the
summary to four spoken chunks, lets the graph decide (`should_call`), and dials.
