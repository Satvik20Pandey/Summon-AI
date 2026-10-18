# Demo video script (≤ 3 minutes)

One story, told end to end: **your agent calls you, you decide, and every other agent knows.**
Record at 1440×900, browser zoom 100%, dark desktop. English subtitles throughout.

## Before recording (5 minutes)

```bash
uv run summon demo-reset --yes          # clean graph + yesterday's memory
uv run summon serve                     # or use https://summon.shravan-ai.tech (mirror: https://summon.77-42-70-237.sslip.io)
```

- Open three windows side by side: **terminal with Claude Code** in `examples/demo_repo`,
  **Memory Lens** (`/lens`), and later a **terminal with Codex**.
- Phone on the desk, ringer on, screen visible to the camera (live mode). In mock mode the
  Lens rings instead — say so on screen ("mock voice") if you use it.
- Prompt ready to paste into Claude Code:
  > Add a DELETE /items/{item_id} route that removes the item and returns {"deleted": item_id}.
  > Add a test and run the tests. Then summarise in 3 lines and ask me exactly one question:
  > "Should DELETE return a 404 status code instead of a 200 with an error body?"

## Shot list

| Time | Screen | What happens | Voice-over / subtitle |
|---|---|---|---|
| 0:00–0:08 | Phone ringing on the desk | Cold open. Pick up. | *(no voice-over — let it ring)* |
| 0:08–0:30 | Phone on speaker + Lens "Calling you" | Summon: "Hi Satvik, this is Summon… Claude Code has finished a task on demo_repo… Should DELETE return a 404 instead of a 200?" You: **"Yes, return a real 404 everywhere in this API. And change GET /items to do the same."** | "My coding agent just finished. Instead of a notification, it called me." |
| 0:30–0:55 | Claude Code terminal | Claude continues on its own, edits `app/main.py`, runs tests, 8 passed. | "What I said on the phone became its next instruction — through Claude Code's own Stop hook." |
| 0:55–1:30 | Memory Lens | Graph grows: Task → Call → Decision / Instruction. Click the green **Decision** → the path highlights Decision → Call → Satvik; panel shows *said on*, *agent running: Claude Code*. | "Every answer is a node in FalkorDB, linked to the exact call. This is the path from a rule back to me." |
| 1:30–2:10 | New terminal: `codex` | Ask Codex: *"Call summon_recall, then tell me how a new route that can miss must respond, and where that rule came from."* Codex quotes the decision, the call id and time, and that Claude Code was running. Back in the Lens: *Read since by Codex*. | "A different agent, a fresh session. It knows what I decided, and why — from one shared memory." |
| 2:10–2:35 | Landing page → "The graph decides" panel, or Lens timeline | Show a `CallDecision`: quiet hours learned from "don't call me before 9" → **RETRY_IN**. | "The graph also decides whether my phone should ring at all." |
| 2:35–3:00 | Landing hero + logo | Title card: *Summon AI — your agents call you, and remember. Built on FalkorDB. Made by Satvik Pandey.* | "One memory, every agent, every decision traceable. Summon AI." |

## If the live call misbehaves

Switch `SUMMON_VOICE=mock`, answer in the Lens, and put a small "mock voice" label on screen
for that segment. Never present a mock call as a real one.

## Recording checklist

- [ ] `demo-reset` run, Lens open, graph shows yesterday's memory
- [ ] Claude Code and Codex both wired (`summon init`), Codex hooks trusted (`/hooks`)
- [ ] `~/.summon/.env` points at the server you are recording against
- [ ] Phone charged, ringer on, Do-Not-Disturb off
- [ ] Subtitles added in the editor; total length ≤ 3:00
- [ ] Upload (YouTube unlisted or Loom), paste the link into README and the submission
