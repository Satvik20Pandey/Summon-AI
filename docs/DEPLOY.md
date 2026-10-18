# Deploying Summon AI

Target: `https://summon.shravan-ai.tech` on the same Hetzner box (Coolify) that runs
Shravan.ai, as a **separate** Coolify resource. Nothing about Shravan.ai changes.

Time needed: about 30 minutes, most of it waiting for builds and DNS.

## 0. What you need

| Item | Where it comes from |
|---|---|
| The repo | `github.com/Satvik20Pandey/Summon-AI` (already connected to Coolify's GitHub app) |
| A DNS record | `summon` A record → the Hetzner box IP (same IP as `api.shravan-ai.tech`) |
| `FALKORDB_PASSWORD` | any long random string (`openssl rand -hex 24`) |
| `SUMMON_API_TOKEN` | any long random string; the same value goes in `~/.summon/.env` on your PC |
| `LLM_API_KEY` | your Groq key (the curator uses `openai/gpt-oss-120b` on Groq) |
| `SHRAVAN_API_KEY` | Shravan console → Developers → create key with scopes `calls:write`, `agents:write`, `webhooks:manage`. Use `sk_test_…` first, then `sk_live_…` |
| `SUMMON_DEVELOPER_PHONE` | your number, `+91…` (stored hashed in the graph; used only to dial you and to recognise you when you call in) |

## 1. Create the Coolify resource

1. Coolify → project → **+ New** → **Docker Compose** → repository `Satvik20Pandey/Summon-AI`,
   branch `main`, compose path `docker-compose.yaml`.
2. **Environment variables** (paste values; names exactly as below):

   ```
   FALKORDB_PASSWORD=...
   SUMMON_API_TOKEN=...
   SUMMON_API_URL=https://summon.shravan-ai.tech
   SUMMON_VOICE=sandbox            # switch to shravan after the sandbox check below
   SUMMON_DEVELOPER_ID=satvik
   SUMMON_DEVELOPER_NAME=Satvik
   SUMMON_DEVELOPER_PHONE=+91...
   SUMMON_TIMEZONE=Asia/Kolkata
   LLM_API_KEY=...
   SHRAVAN_API_KEY=sk_test_...
   ```
   `SHRAVAN_AGENT_ID` and `SHRAVAN_WEBHOOK_SECRET` are added in step 3.
3. **Domains**: on the `summon-api` service set `https://summon.shravan-ai.tech:8000`
   (the `:8000` pins the container port). Leave `falkordb` without a domain — it is only on the
   private compose network.
4. Keep **Preview Deployments off**. Deploy.
5. Check: `curl https://summon.shravan-ai.tech/api/health` →
   `{"ok":true,"voice":"sandbox","falkordb":true,...}`.

## 2. Seed the demo memory (optional, recommended)

Coolify → `summon-api` → **Terminal**:

```bash
uv run summon demo-seed
```

Open `https://summon.shravan-ai.tech/lens` — the graph should show yesterday's memory.

## 3. Connect the voice provider

Still in the `summon-api` terminal:

```bash
uv run summon setup
```

It creates the two Summon voice agents on Shravan.ai from `summon/voice/flows.py` and registers
`https://summon.shravan-ai.tech/api/webhooks/shravan` for `call.completed`, `call.analyzed` and
`call.failed`. It prints:

```
SHRAVAN_AGENT_ID=agent_...
SHRAVAN_WEBHOOK_SECRET=whsec_...
# inbox flow (set as the business's inbound flow in the console): <flow id>
```

Add the two variables in Coolify and **redeploy**.

## 4. Sandbox check (free, no phone)

With `SUMMON_VOICE=sandbox` and the `sk_test_` key, from your PC:

```bash
# ~/.summon/.env on your PC
SUMMON_API_URL=https://summon.shravan-ai.tech
SUMMON_API_TOKEN=<same token as in Coolify>
```

Then in a wired project (`uv run summon init <dir>`) finish any Claude Code task. Shravan's
sandbox answers in about 4 seconds with a canned transcript and sends real signed webhooks. The
Lens timeline should show *Calling you → Call analyzed*, and the graph gains a Call node.

## 5. Go live

1. Coolify: `SHRAVAN_API_KEY=sk_live_…`, `SUMMON_VOICE=shravan`. Run `uv run summon setup`
   once more with the live key (webhook endpoints are per mode), update `SHRAVAN_AGENT_ID` and
   `SHRAVAN_WEBHOOK_SECRET`, redeploy.
2. Make sure the Shravan wallet has balance (₹500 covers testing and the demo).
3. Finish a task in a wired project: your phone rings.

## 6. Inbound "summon by phone" (optional)

Shravan console → business settings → **Inbound flow** → the `summon_inbox` flow id printed by
`summon setup`. Calling the business number now reaches Summon instead of the Aarohan demo;
switch it back after the hackathon. On your PC run the inbox daemon in the project you want
phoned-in tasks to run in:

```bash
uv run summon daemon demo_repo --path examples/demo_repo --agent claude
```

Unknown callers are rejected; only `SUMMON_DEVELOPER_PHONE` is accepted.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/api/health` shows `"falkordb": false` | `FALKORDB_PASSWORD` differs between the two services, or `falkordb` is unhealthy (check its logs). |
| Calls never become *analyzed* | Webhook not delivered: check Shravan console → Developers → webhook deliveries. The poll fallback resolves calls after ~20 s anyway. |
| `401 bad signature` in API logs | `SHRAVAN_WEBHOOK_SECRET` is from the other mode (test vs live). Re-run `summon setup`. |
| Hook does nothing | `~/.summon/.env` on your PC points at the wrong URL or token; see `<project>/.summon/hook.log`. |
| Phone does not ring at night | Working as designed: a quiet-hours preference is in the graph. The Lens shows the `CallDecision` and its reason. |
