# Submission text (copy into the Graph Hacks form)

**Project name:** Summon AI

**Tagline:** Your coding agents call you — and every decision you make on the phone becomes shared,
traceable memory in FalkorDB.

**Tracks:** Track 02 — Agent Memory and Coordination (primary); Track 01 — Agents That Act on
Connected Data.

**Repository:** https://github.com/Satvik20Pandey/Summon-AI

**Live demo:** https://summon.shravan-ai.tech (Memory Lens at `/lens`) — mirror: https://summon.77-42-70-237.sslip.io

**Demo video:** _(paste link)_

## Description (≈200 words)

When Claude Code or Codex finishes a task, Summon AI phones the developer, reads a summary, asks
the agent's open question and takes a spoken instruction. Through the agent's own Stop hook, those
words become its next instruction, so the agent carries on without anyone touching a keyboard.

Everything said on the call is stored in FalkorDB as a graph: tasks, calls, answers, instructions,
decisions and preferences, each linked to the call it came from. One graph per developer is
shared by every agent through a single MCP server. A fresh Codex session can ask what the
developer decided and get the rule, the call it was said on, and the agent that was running at
the time. A changed mind becomes a `SUPERSEDES` edge, and every read leaves a `READ` edge.

The graph also acts. Quiet hours learned on an earlier call, missed calls and task priority
decide whether the phone rings now, retries later or skips. That decision is written back as a
node with its reason.

The Memory Lens UI shows the live graph, highlights the provenance path from any decision back
to the developer, and lets anyone run the whole loop in mock voice mode without a telephony
account.

## FalkorDB usage

- Primary and only database for agent memory (one graph per developer).
- Multi-hop Cypher for provenance, handoff between agents, briefs, supersession, call policy.
- `shortestPath` for the provenance path; full-text indexes for recall; MERGE-based idempotent
  writes for webhooks.
- Queries: `summon/graph/queries.py`, documented in `docs/CYPHER.md`.
- Graph model: `docs/GRAPH_MODEL.md`.

## Disclosure

- AI coding assistants were used while building; all design and code were reviewed by the
  author.
- Voice calls use the public developer API of Shravan.ai, the author's pre-existing voice
  product, as a telephony provider only. No Shravan.ai code is in the repo. Mock mode runs
  without it.
- The UI follows the Shravan.ai design language.

**Made by Satvik Pandey**
