# Graph model

One FalkorDB graph per developer, named `summon_<developer_id>`. Everything an agent or
a phone call produces is a node; every fact that came from a call keeps an edge to that
call. Nothing is overwritten: a newer decision on the same topic closes the old one
(`until`) and links `SUPERSEDES`.

```
(:Developer {id, name, phone_hash, quiet_from, quiet_to})
(:Project   {id, name, path})
(:Agent     {name: claude-code | codex | cursor, model})

(:Session {id, started_ts, started_at})-[:RUN_BY]->(:Agent)
(:Session)-[:ON]->(:Project)

(:Task {id, title, summary, status, priority, started_ts, done_ts, done_at})-[:IN]->(:Session)
(:Task)-[:TOUCHED]->(:File {path})
(:Task)-[:RAISED]->(:Question {id, text, asked_ts})

(:Call {id, provider_call_id, direction, status, ts, at, duration_s, disposition,
        transcript, recording_url, script})-[:ABOUT]->(:Task)
(:Call)-[:WITH]->(:Developer)

(:Answer {id, text, ts})-[:TO]->(:Question)
(:Answer)-[:SAID_IN]->(:Call)

(:Instruction {id, text, status: pending|running|done, ts, at})-[:GIVEN_IN]->(:Call)
(:Instruction)-[:FOR]->(:Project)
(:Instruction)-[:EXECUTED_AS]->(:Task)              // the loop closes

(:Decision {id, text, topic, applies_to, since_ts, since, until_ts, until})-[:MADE_IN]->(:Call)
(:Decision)-[:APPLIES_TO]->(:Project | :File)
(:Decision)-[:SUPERSEDES]->(:Decision)
(:Decision {source:'agent'})-[:WRITTEN_BY]->(:Agent)   // summon_remember
(:Decision)-[:IN_SESSION]->(:Session)

(:Preference {id, key, value, since_ts, until_ts})-[:OF]->(:Developer)
(:Preference)-[:LEARNED_IN]->(:Call)

(:Agent)-[:READ {at_ts, at}]->(:Decision | :Preference | :Task | :Instruction)   // who consumed what

(:CallDecision {id, action: call|retry_in|skip, reason, inputs, ts, at})-[:FOR]->(:Task)   // why the phone rang (or not)
(:Event {id, ts})                                    // webhook idempotency gate
```

## Indexes

Created idempotently by `summon/graph/schema.py` on first use of a graph:

- Range: `Task(id, done_ts)`, `Call(id, provider_call_id, ts, status)`, `Instruction(id, status)`,
  `Decision(id, topic)`, `Preference(key)`, `Session(id)`, `Question(id)`, `Event(id)`,
  `Project(id)`, `Developer(id)`, `Agent(name)`.
- Full-text (`CALL db.idx.fulltext.createNodeIndex`): `Decision.text`, `Instruction.text`,
  `Task.summary`.

## Why a graph and not a table of transcripts

- **Provenance is a path.** "Where did this rule come from?" is
  `Decision -[:MADE_IN]-> Call -[:WITH]-> Developer`, plus `Call -[:ABOUT]-> Task -[:IN]->
  Session -[:RUN_BY]-> Agent` for "who was running when you said it". One query, no joins
  to design.
- **Handoff is a relationship between sessions.** "What did any other agent do since my last
  session" is a pattern over `Session -[:RUN_BY]-> Agent` with a timestamp filter.
- **Supersession keeps history.** `SUPERSEDES` edges mean the current decision and its
  predecessors are both answerable.
- **The policy reads the graph it writes.** `should_call` reads preferences and recent calls
  and writes a `CallDecision` node, so the UI can explain the agent's behaviour from the same
  store.
