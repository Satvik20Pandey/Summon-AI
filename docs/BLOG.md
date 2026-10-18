# My coding agents call me now — and they remember what I said

*By Satvik Pandey · built for Graph Hacks (WeMakeDevs × FalkorDB), October 2026*

Every developer who works with coding agents knows the loop. You give Claude Code a task, you
go and do something else, and twenty minutes later you come back to a terminal that has been
waiting for you since minute four. It finished, or it had a question, and it just sat there.

The obvious fix is a notification. I wanted something better: **the agent should call me.**
Not a ping I'll ignore — a phone call, where it tells me what it did, asks the one question it
actually needs answered, and takes my instruction. Then it should carry on with what I said.

That part is easy to demo and has been done before. The interesting problem is what happens to
the things I say on that call.

## The problem with phone calls is the same as the problem with chats

When I tell an agent on the phone "keep the old login route backward compatible", that is a
**decision about the project**. It should still be true tomorrow, when a different agent — say
Codex, in a fresh session — touches the same code. Today, that sentence would live in a call
transcript nobody will read, or in one agent's context window that disappears when the session
ends.

So Summon AI has two halves:

1. **A phone line.** When Claude Code or Codex stops, its Stop hook hands Summon a summary and
   its open questions. Summon places a call (through the public API of
   [Shravan.ai](https://shravan-ai.tech), the Indian voice-agent platform I run), reads the
   summary, asks, listens, and hands my words back to the agent as its next instruction.
2. **A memory, in FalkorDB.** Everything that happens — tasks, calls, answers, instructions,
   decisions, preferences, and which agent read what — is stored as a graph. One graph per
   developer, shared by every agent through a single MCP server.

## Why a graph and not a table of transcripts

I started by writing down the questions an agent actually asks before it starts work:

- *What did the developer decide about this project, and when?*
- *What did the other agents do while I was away?*
- *Where did this rule come from — can I trust it?*
- *Did the developer change their mind since?*

Every one of those is a relationship question. In Cypher they are one or two hops:

```cypher
// where did this rule come from?
MATCH (d:Decision {id: $id})-[:MADE_IN]->(c:Call)-[:ABOUT]->(t:Task)
      -[:IN]->(:Session)-[:RUN_BY]->(a:Agent)
RETURN d.text, c.at, t.title, a.name
```

```cypher
// what did the other agents do since my last session?
MATCH (t:Task)-[:IN]->(s:Session)-[:RUN_BY]->(a:Agent)
WHERE a.name <> $me AND t.done_ts >= $since
RETURN t.title, a.name
```

A changed mind is a `SUPERSEDES` edge: the new decision closes the old one (`until`), so the
history stays answerable. "Which agents have seen this rule?" is a `READ` edge that every
recall writes. None of this needed a schema migration or a join table — I added edges as the
product needed them.

The graph also decides **whether my phone should ring**. If I once said "don't call me before
9", that became a `Preference` linked to the call I said it on. The call policy reads it,
together with how many calls I missed in the last hour and how urgent the task is, and writes
its decision back as a `CallDecision` node — so the UI can always show *why* the phone did or
did not ring.

## The moment it clicked

The test that convinced me was not the phone call. It was this:

1. Claude Code finished adding a `DELETE /items` route and asked whether it should return a 404
   instead of a 200 with an error body.
2. I answered: "Yes, a real 404 everywhere in this API — and change `GET /items` the same way."
3. Claude did it. Eight tests green.
4. I opened a **fresh Codex session** and asked it how a new route that can miss should respond.

Codex called `summon_recall`, and answered with my sentence, the call id, the time I said it,
and the fact that Claude Code was the agent running at the time. Then the Memory Lens showed a
new edge: *Codex READ that decision*.

That is what I mean by agents that remember: not one agent with a longer context window, but
many agents with one shared, explainable memory — with me in it.

## Details that mattered

- **The phone fragments speech.** A 400 ms pause splits a spoken instruction into separate
  turns. So Summon never trusts per-turn capture; a curator reads the whole transcript after the
  call and extracts answers, instructions, decisions and preferences as structured data.
- **Webhooks arrive twice.** Every write is a `MERGE`, and every webhook event id goes through a
  `MERGE (:Event {id})` gate. Redelivery is a no-op.
- **Hooks must never break the agent.** The Stop hook guards `stop_hook_active`, keeps one call
  per task, has a hard time budget, and exits quietly on any error.
- **FalkorDB is fast enough to sit in the loop.** The full brief an agent reads before starting
  is five multi-hop queries and comes back in about 15 ms (p50; a single hop is about 2 ms), so
  memory costs the agent nothing it would notice.
- **Mock mode.** `SUMMON_VOICE=mock` turns the phone into a panel in the Memory Lens, so anyone
  can run the whole loop locally without a telephony account.

## Try it

The code, the graph model and every Cypher query are on GitHub:
[github.com/Satvik20Pandey/Summon-AI](https://github.com/Satvik20Pandey/Summon-AI).
Clone it, start FalkorDB, run `summon init` in a project, and let your agent call you.

*Summon AI is part of the Shravan.ai ecosystem. Built with FalkorDB.*
