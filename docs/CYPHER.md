# The Cypher the product depends on

All of these live in `summon/graph/queries.py` and `summon/graph/memory.py`; they are
reproduced here so a reviewer can paste them into FalkorDB Browser against the
`summon_<developer>` graph. Parameters are `$name`.

## 1. Provenance: where did this memory come from?

```cypher
MATCH (n {id: $id})
OPTIONAL MATCH (n)-[:MADE_IN|GIVEN_IN|LEARNED_IN|SAID_IN]->(c:Call)
OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)-[:IN]->(s:Session)-[:RUN_BY]->(a:Agent)
OPTIONAL MATCH (n)-[:SUPERSEDES]->(old)
RETURN n, c, t, a.name, old
```
```cypher
-- who has consumed it since
MATCH (r:Agent)-[rd:READ]->(n {id: $id})
RETURN r.name, rd.at ORDER BY rd.at_ts DESC
```
```cypher
-- the path the UI highlights (FalkorDB: shortestPath goes in WITH/RETURN and is directed)
MATCH (n {id: $id}), (d:Developer)
WITH shortestPath((n)-[*..4]->(d)) AS p
WHERE p IS NOT NULL
RETURN [x IN nodes(p) | coalesce(x.id, x.path, x.name)]
```
Sample: `["dec_3f1c…", "call_9d3d…", "satvik"]` → "you said this on call `call_9d3d…` at 17:38".

## 2. Handoff: what did other agents do since my last session?

```cypher
-- my previous session start (the latest one is the session I am in now)
MATCH (s:Session)-[:RUN_BY]->(:Agent {name: $me}), (s)-[:ON]->(:Project {id: $pid})
RETURN s.started_ts ORDER BY s.started_ts DESC LIMIT 2
```
```cypher
MATCH (t:Task)-[:IN]->(s:Session)-[:ON]->(:Project {id: $pid}), (s)-[:RUN_BY]->(a:Agent)
WHERE a.name <> $me AND t.done_ts >= $since
RETURN t, a.name, s.started_at ORDER BY t.done_ts DESC LIMIT 10
```
```cypher
MATCH (d:Decision)-[:APPLIES_TO]->(:Project {id: $pid}), (d)-[:MADE_IN]->(c:Call)
WHERE d.since_ts >= $since AND d.until_ts IS NULL
OPTIONAL MATCH (c)-[:ABOUT]->(t:Task)-[:IN]->(:Session)-[:RUN_BY]->(a:Agent)
RETURN d, c.id, c.at, a.name ORDER BY d.since_ts DESC
```
Then the reader is recorded:
```cypher
MERGE (a:Agent {name: $agent})
WITH a UNWIND $ids AS nid
MATCH (n) WHERE n.id = nid AND (n:Decision OR n:Preference OR n:Task OR n:Instruction)
MERGE (a)-[r:READ]->(n) SET r.at_ts = $ts, r.at = $iso
```

## 3. Brief: what any agent reads before touching a project

```cypher
MATCH (t:Task)-[:IN]->(s:Session)-[:ON]->(p:Project {id: $pid})
OPTIONAL MATCH (s)-[:RUN_BY]->(a:Agent)
RETURN t, a.name ORDER BY t.done_ts DESC LIMIT 3
```
```cypher
MATCH (t:Task)-[:IN]->(:Session)-[:ON]->(:Project {id: $pid}), (t)-[:RAISED]->(q:Question)
WHERE NOT (q)<-[:TO]-(:Answer)
RETURN q, t.title
```
```cypher
MATCH (d:Decision)-[:APPLIES_TO]->(:Project {id: $pid})
WHERE d.until_ts IS NULL
OPTIONAL MATCH (d)-[:MADE_IN]->(c:Call)
RETURN d, c.id, c.at ORDER BY d.since_ts DESC
```
```cypher
MATCH (i:Instruction)-[:FOR]->(:Project {id: $pid}) WHERE i.status = 'pending'
OPTIONAL MATCH (i)-[:GIVEN_IN]->(c:Call)
RETURN i, c.id, c.at ORDER BY i.ts
```

## 4. Supersession: a new decision closes the old one, history stays

```cypher
MATCH (c:Call {id: $cid})
CREATE (d:Decision {id: $did, text: $text, topic: $topic, since_ts: $ts, since: $iso})
CREATE (d)-[:MADE_IN]->(c)
```
```cypher
MATCH (old:Decision {topic: $topic})-[:APPLIES_TO]->(p:Project {id: $pid})
WHERE old.id <> $did AND old.until_ts IS NULL
SET old.until_ts = $ts, old.until = $iso
WITH old MATCH (d:Decision {id: $did})
MERGE (d)-[:SUPERSEDES]->(old)
```

## 5. Should the phone ring? (Track 01)

Inputs are read from the graph, the decision is written back:
```cypher
MATCH (p:Preference {key: 'quiet_hours'})-[:OF]->(:Developer {id: $dev})
WHERE p.until_ts IS NULL RETURN p.value LIMIT 1
```
```cypher
MATCH (c:Call)-[:WITH]->(:Developer {id: $dev})
WHERE c.ts >= $since AND c.direction = 'outbound'
RETURN count(c), sum(CASE WHEN c.status = 'failed' THEN 1 ELSE 0 END)
```
```cypher
MATCH (t:Task {id: $tid})
CREATE (x:CallDecision {id: $cid, action: $action, reason: $reason, inputs: $inputs, ts: $ts})
CREATE (x)-[:FOR]->(t)
```
Policy (Python, `queries.should_call`): over the hourly cap → `skip`; inside quiet hours and
the task is not urgent → `retry_in` until quiet hours end; two or more unanswered calls in the
last hour → `retry_in` 30 min; otherwise `call`.

## 6. Idempotency gate for webhooks

```cypher
MERGE (e:Event {id: $id})
ON CREATE SET e.fresh = true, e.ts = $ts
ON MATCH SET e.fresh = false
RETURN e.fresh
```
A redelivered `evt_…` returns `false` and is ignored; one call, one set of facts.

## 7. Recall (full-text)

```cypher
CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node, score
RETURN node, score LIMIT 8
```
`$q` is the developer's words turned into `backward* | compat* | login*`.

## FalkorDB specifics we hit

- `shortestPath()` must be in a `WITH`/`RETURN`, not inside `MATCH`, and only directed
  traversals are supported — hence every provenance edge points *toward* the Call and the
  Developer.
- `CREATE INDEX` / `createNodeIndex` raise when the index exists; `schema.py` wraps each.
- `MERGE … ON CREATE SET … ON MATCH SET` works and is what makes retries safe.
