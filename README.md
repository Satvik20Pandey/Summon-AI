# Summon AI

**Your coding agents share one memory — and every decision traces back to you, by phone.**

When Claude Code or Codex finishes a task, Summon phones you, reads the summary, asks its open
question and takes your spoken instruction. The agent continues with what you said. A different
agent, in a fresh session, retrieves that decision from FalkorDB together with the call it came from,
and respects it.

Built for [Graph Hacks](https://www.wemakedevs.org/hackathons/falkordb) (WeMakeDevs × FalkorDB),
Oct 15–18, 2026. Tracks entered: **02 Agent Memory and Coordination** (primary) and
**01 Agents That Act on Connected Data**.

> Status: under construction during the hackathon. Sections below are filled in as they land.

## How it works

```
Claude Code finishes a task ──► Summon calls you ──► "Keep the API backward-compatible"
        │                                                      │
        │  Stop hook feeds your words back to the agent        ▼
        ▼                                     (:Instruction)-[:GIVEN_IN]->(:Call)   (:Decision)-[:MADE_IN]->(:Call)
Claude continues with the instruction                          │
                                                               ▼
Fresh Codex session ──► summon_recall(project) ──► Decision + "said on the call at 14:32" ──► respects it
```

## Graph model

_(coming)_

## Cypher the product depends on

_(coming)_

## Run it locally in 5 minutes (no accounts needed)

_(coming)_

## Disclosure

- Built with help from Claude Code (Anthropic). Every query, hook and architectural choice was
  reviewed and is explained in this README.
- Voice calls are placed through the public developer API of [Shravan.ai](https://shravan-ai.tech),
  a pre-existing voice-agent product by the same author, used here only as a telephony provider
  (`POST /v1/calls` + signed webhooks), the way any customer would. No Shravan.ai code is included.
  `SUMMON_VOICE=mock` runs the whole loop without it.
- Prior art we build on: [call-me](https://github.com/ZeframLou/call-me) and
  [awesome-claude-call](https://github.com/PatterAI/awesome-claude-call) ring your phone when
  Claude finishes; they keep no memory and serve one agent. Graph memory for agents exists
  (Graphiti, Mem0, Cognee on FalkorDB); none has a human on the phone in the loop.

## License

MIT
