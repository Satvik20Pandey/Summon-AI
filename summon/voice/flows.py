"""Conversation flows for the Shravan.ai voice provider, built from its public flow
format (https://shravan-ai.tech/docs). Nothing here is provider code; it is the JSON
document a customer uploads to describe what the agent says and how it routes.

Format recap: node types initial|node|end; a node speaks (`tts_say`), listens
(`function`) and routes by the outcome enum its router returns; `task_messages` tell
the router how to classify the reply. Spoken text uses single-brace {variables}
filled from per-call flow_vars (≤20 keys × ≤200 chars)."""

from __future__ import annotations

from typing import Any

from summon.voice.base import MAX_CHUNKS, MAX_QUESTIONS

VARIABLES = ["bot", "project", "task_title", "n_q"] + \
            [f"s{i}" for i in range(1, MAX_CHUNKS + 1)] + \
            [f"q{i}" for i in range(1, MAX_QUESTIONS + 1)]


def _say(node_id: str, text: str, outcomes: list[str], routes: dict[str, str],
         default: str, task: str, node_type: str = "node") -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "data": {
            "task_messages": [{"role": "system", "content": task}],
            "functions": [{
                "name": f"route_{node_id}",
                "properties": {"outcome": {"type": "string", "enum": outcomes}},
                "required": ["outcome"],
                "decision": {
                    "conditions": [{"value": v, "next_node_id": n} for v, n in routes.items()],
                    "default_next_node_id": default,
                },
            }],
            "post_actions": [
                {"type": "tts_say", "text": text, "prompts": {"en": text}},
                {"type": "function"},
            ],
            "reanchor": "none",
        },
    }


def _end(node_id: str, text: str) -> dict[str, Any]:
    return {
        "id": node_id, "type": "end",
        "data": {"functions": [], "post_actions": [
            {"type": "tts_say", "text": text, "prompts": {"en": text}},
            {"type": "end_conversation"},
        ]},
    }


_ANSWER_TASK = (
    "You asked the developer a question (see your last turn). Any real answer -> answered, "
    "value = their answer in their own words. 'skip', 'don't know', 'later' -> answered, "
    "value = skip. Unclear or cut off -> answered=false. A question about the work -> knowledge."
)


def summon_done_flow() -> dict[str, Any]:
    """Outbound: report a finished task, ask up to three questions, take instructions."""
    greet = ("Hi {name}, this is Summon, an AI assistant calling for {bot}. {task_title} on "
             "{project} is done. {s1} {s2} {s3} {s4} I have {n_q} quick questions, do you have "
             "a minute?")
    nodes = [
        _say("greet", greet, ["ready", "busy", "knowledge"],
             {"ready": "ask_q1", "busy": "busy_bye"}, "ask_q1",
             "You told the developer a coding task is done, read a short summary and asked if "
             "they have a minute. yes/ok/sure/go ahead -> ready. busy/later/call back/not now "
             "-> busy. A question about the work -> knowledge.", node_type="initial"),
        _say("ask_q1", "First question. {q1}", ["answered", "knowledge"],
             {"answered": "ask_q2"}, "ask_q2", _ANSWER_TASK),
        _say("ask_q2", "{q2}", ["answered", "knowledge"],
             {"answered": "ask_q3"}, "ask_q3", _ANSWER_TASK),
        _say("ask_q3", "{q3}", ["answered", "knowledge"],
             {"answered": "ask_more"}, "ask_more", _ANSWER_TASK),
        _say("ask_more", "Anything else you want {bot} to do next?",
             ["instructions", "none", "knowledge"],
             {"instructions": "confirm", "none": "bye"}, "confirm",
             "You asked for any other instructions. Any instruction or request -> "
             "instructions, value = the instruction verbatim. no/nothing/that's all -> none. "
             "A question about the work -> knowledge."),
        _say("confirm", "To confirm: {ask_more_answer}. Did I get that right?",
             ["correct", "change", "knowledge"],
             {"correct": "bye", "change": "ask_more"}, "bye",
             "You read back the developer's instruction. yes/right/correct/that's it -> "
             "correct. They want to change or add something -> change."),
        _end("bye", "Perfect, I'll pass that on to {bot} right away. Bye!"),
        _end("busy_bye", "No problem, I'll call back later. Bye!"),
    ]
    return {
        "meta": {"name": "Summon: task done", "version": "1.0.0"},
        "languages": ["en"],
        "variables": VARIABLES,
        "entry_nodes": ["greet"],
        "edges": [],
        "nodes": nodes,
    }


def summon_inbox_flow() -> dict[str, Any]:
    """Inbound: the developer phones in to dictate a task for their agents."""
    nodes = [
        _say("welcome", "Summon here, the AI assistant for your coding agents. Which project is "
             "this about, and what should they do?", ["captured", "knowledge"],
             {"captured": "capture_task"}, "capture_task",
             "The caller names a project and may start describing a task. Any project name or "
             "task -> captured, value = what they said verbatim. Unclear -> answered=false.",
             node_type="initial"),
        _say("capture_task", "Got it. Tell me exactly what to do, and say 'that's all' when "
             "you're done.", ["instructions", "done", "knowledge"],
             {"instructions": "capture_task", "done": "confirm"}, "confirm",
             "The caller is dictating a task. More instruction text -> instructions, value = "
             "the text verbatim. 'that's all', 'done', 'go' -> done."),
        _say("confirm", "I'll queue that for your agents and call you back when it's done. "
             "Anything to add?", ["correct", "change", "knowledge"],
             {"correct": "bye", "change": "capture_task"}, "bye",
             "no/nothing/that's it -> correct. Anything to add or change -> change."),
        _end("bye", "On it. Talk soon!"),
    ]
    return {
        "meta": {"name": "Summon: inbox", "version": "1.0.0"},
        "languages": ["en"],
        "variables": [],
        "entry_nodes": ["welcome"],
        "edges": [],
        "nodes": nodes,
    }


# --------------------------------------------------------------------------- self-check

def validate(flow: dict[str, Any]) -> list[str]:
    """The provider's documented hard rules, checked before upload so a bad flow never
    costs a call: every placeholder declared, every node reaches an end, greeting says AI."""
    import re

    problems: list[str] = []
    ids = {n["id"] for n in flow["nodes"]}
    declared = set(flow.get("variables", [])) | {"name", "client_name", "customer_name"}
    declared |= {f"{nid}_answer" for nid in ids}
    ends = {n["id"] for n in flow["nodes"] if n["type"] == "end"}
    nxt: dict[str, set[str]] = {}
    for n in flow["nodes"]:
        for pa in n["data"]["post_actions"]:
            if pa["type"] == "tts_say":
                for var in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", pa["text"]):
                    if var not in declared:
                        problems.append(f"{n['id']}: undeclared variable {{{var}}}")
        outs: set[str] = set()
        for fn in n["data"].get("functions", []):
            dec = fn["decision"]
            for c in dec["conditions"]:
                outs.add(c["next_node_id"])
            outs.add(dec["default_next_node_id"])
        nxt[n["id"]] = outs
        for o in outs:
            if o not in ids:
                problems.append(f"{n['id']}: dangling edge to {o}")
    # reachability to an end node
    for start in ids:
        seen, stack = set(), [start]
        ok = False
        while stack:
            cur = stack.pop()
            if cur in ends:
                ok = True
                break
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(nxt.get(cur, ()))
        if not ok:
            problems.append(f"{start}: cannot reach an end node")
    entry = next(n for n in flow["nodes"] if n["id"] == flow["entry_nodes"][0])
    first = entry["data"]["post_actions"][0]["text"].lower()
    if not any(w in first for w in ("ai ", "ai assistant", "assistant", "automated")):
        problems.append("entry node must disclose it is an AI")
    return problems
