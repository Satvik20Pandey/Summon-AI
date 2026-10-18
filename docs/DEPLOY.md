# Deploying Summon AI

Production runs on the same Hetzner box as Shravan.ai, behind the Traefik proxy that Coolify
already manages there, as a plain Docker Compose stack in `/opt/summon-ai`. This is the
procedure that was actually used; it needs SSH to the box and nothing else.

- Live: `https://summon.shravan-ai.tech` (needs the DNS record in step 1)
- Always-on mirror, no DNS needed: `https://summon.77-42-70-237.sslip.io`

## 0. What you need

| Item | Where it comes from |
|---|---|
| SSH | `ssh -i ~/.ssh/hetzner_shravan root@77.42.70.237` |
| `FALKORDB_PASSWORD`, `SUMMON_API_TOKEN` | generated once with `openssl rand -hex 24` (in `/opt/summon-ai/.env`) |
| `LLM_API_KEY` | the Groq key already in the Shravan.ai API container (`docker exec <api> printenv GROQ_API_KEY`) |
| `SHRAVAN_API_KEY` | minted for the `aarohan_learning` business from inside the Shravan.ai API container with `create_api_key(...)`, scopes `calls:write agents:write webhooks:manage knowledge:read usage:read`; test and live keys are kept in `/opt/summon-ai/.secrets/shravan_keys` (root-only) |
| `SUMMON_DEVELOPER_PHONE` | the developer's number, `+91…` (stored hashed in the graph) |

## 1. DNS (GoDaddy)

Add an **A** record `summon` → `77.42.70.237` (same IP as `api`). Traefik requests the
Let's Encrypt certificate automatically the first time the name resolves; until then the
sslip.io mirror serves the same stack.

## 2. First deploy

```bash
ssh -i ~/.ssh/hetzner_shravan root@77.42.70.237
cd /opt && git clone https://github.com/Satvik20Pandey/Summon-AI summon-ai && cd summon-ai
umask 077; cp .env.example .env         # then fill the values from the table above
docker compose -f docker-compose.yaml -f docker-compose.hetzner.yaml up -d --build
curl -s https://summon.77-42-70-237.sslip.io/api/health
```

`docker-compose.hetzner.yaml` joins the `coolify` network and carries the Traefik labels for
both hostnames (one router each, so a missing DNS record for one never blocks the other's
certificate). Only ports 22/80/443 are open on the box; everything goes through Traefik.

## 3. Connect the voice provider

```bash
docker compose -f docker-compose.yaml -f docker-compose.hetzner.yaml exec -T summon-api \
  /srv/.venv/bin/summon setup --webhook-url https://summon.shravan-ai.tech/api/webhooks/shravan
```

It uploads the two conversation flows as Shravan.ai agents and registers the webhook for
`call.completed`, `call.analyzed`, `call.failed`, then prints `SHRAVAN_AGENT_ID` and
`SHRAVAN_WEBHOOK_SECRET`. Put both in `.env` and restart the API:

```bash
docker compose -f docker-compose.yaml -f docker-compose.hetzner.yaml up -d summon-api
```

Webhook endpoints are per key mode: run `setup` once with the test key (sandbox) and again
after switching to the live key.

## 4. Sandbox check (free, no phone)

`SUMMON_VOICE=sandbox` with the `sk_test_` key: Shravan answers each call in a few seconds
with a canned transcript and sends real signed webhooks. Place one from the box:

```bash
TOK=$(grep ^SUMMON_API_TOKEN= .env | cut -d= -f2-)
curl -s -X POST https://summon.shravan-ai.tech/api/calls -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"project":"demo-api","agent":"claude-code",
  "session_id":"s1","task_id":"t1","title":"Smoke","summary":"Did a thing.","questions":["Ship it?"]}'
```

`GET /api/calls/<id>?wait=120` returns `status: analyzed` with the transcript and the facts
the curator extracted. The Lens timeline shows *Calling you → Call analyzed*.

## 5. Go live

```bash
cd /opt/summon-ai
L=$(grep ^LIVE= .secrets/shravan_keys | cut -d= -f2-)
sed -i "s|^SHRAVAN_API_KEY=.*|SHRAVAN_API_KEY=$L|; s|^SUMMON_VOICE=.*|SUMMON_VOICE=shravan|; s|^SUMMON_DEVELOPER_PHONE=.*|SUMMON_DEVELOPER_PHONE=+91XXXXXXXXXX|" .env
docker compose -f docker-compose.yaml -f docker-compose.hetzner.yaml up -d summon-api
# register the live-mode agents + webhook, then paste the new id/secret and restart (step 3)
```

The `aarohan_learning` wallet pays for calls (API rate = console rate + 20%).

## 6. Your PC

`~/.summon/.env` tells the hooks and the MCP server where the API is:

```
SUMMON_API_URL=https://summon.shravan-ai.tech
SUMMON_API_TOKEN=<same token as on the box>
```

Then `uv run summon init <project>` in any project you want wired.

## 7. Inbound "summon by phone" (optional)

Set `aarohan_learning_summon_inbox` as the `aarohan_learning` business's inbound flow (console
→ Settings → Inbound flow, or the admin route `POST /api/businesses/aarohan_learning/inbound-flow`).
The business number (+91 80318 25160) then reaches Summon instead of the Aarohan demo; revert
to `aarohan_learning_phone_counselling` after the hackathon. Run the inbox daemon on your PC:

```bash
uv run summon daemon demo_repo --path examples/demo_repo --agent claude
```

## Updating

```bash
cd /opt/summon-ai && git pull && docker compose -f docker-compose.yaml -f docker-compose.hetzner.yaml up -d --build
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/api/health` shows `"falkordb": false` | `FALKORDB_PASSWORD` changed after the volume was created, or the `falkordb` container is unhealthy (`docker logs summon-ai-falkordb-1`). |
| Calls never become *analyzed* | Webhook not delivered: Shravan console → Developers → deliveries. The poll fallback resolves calls after ~20 s anyway. |
| `401 bad signature` in API logs | `SHRAVAN_WEBHOOK_SECRET` is from the other key mode. Re-run `summon setup`. |
| Hook does nothing on your PC | `~/.summon/.env` points at the wrong URL or token; see `<project>/.summon/hook.log`. |
| Phone does not ring at night | Working as designed: a quiet-hours preference is in the graph. The Lens shows the `CallDecision` and its reason. |
| Certificate error on `summon.shravan-ai.tech` | DNS record missing or not propagated yet; use the sslip.io mirror meanwhile. |
