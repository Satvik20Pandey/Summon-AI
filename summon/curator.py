"""The curator turns language into graph facts.

Two jobs:
1. ``condense``: fit an agent's task summary and questions into what a phone call can
   carry (4 spoken chunks of ≤200 chars, ≤3 questions).
2. ``extract``: read a finished call's transcript and return answers, instructions,
   decisions and preferences as structured data for the graph.

Both use an OpenAI-compatible LLM when ``LLM_API_KEY`` is set and fall back to
deterministic heuristics otherwise, so mock mode needs no key at all.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from summon.config import get_settings
from summon.voice.base import MAX_CHUNKS, MAX_QUESTIONS, MAX_VAR

log = logging.getLogger(__name__)


@dataclass
class Extraction:
    answers: list[dict[str, str]] = field(default_factory=list)     # {question_id, text}
    instructions: list[str] = field(default_factory=list)
    decisions: list[dict[str, str]] = field(default_factory=list)   # {text, topic, applies_to}
    preferences: list[dict[str, str]] = field(default_factory=list) # {key, value}
    busy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# --------------------------------------------------------------------------- LLM plumbing

def _client():
    s = get_settings()
    if not s.llm_api_key:
        return None
    from openai import OpenAI

    return OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)


def _chat_json(system: str, user: str) -> dict[str, Any] | None:
    client = _client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=get_settings().llm_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        log.warning("curator LLM call failed, using heuristics: %s", exc)
        return None


# --------------------------------------------------------------------------- condense

_CONDENSE_SYS = (
    "You write what an AI assistant will SAY on a short phone call to a software developer. "
    "Rewrite the task summary as plain spoken English: no markdown, no code blocks, no file "
    "paths longer than a few words, numbers as words where natural. Return JSON: "
    '{"chunks": [up to 4 strings, each under 180 characters, together under 650 characters], '
    '"questions": [up to 3 short spoken questions, each under 180 characters]}. '
    "Keep the questions' meaning exactly; drop any question beyond three."
)


def condense(summary: str, questions: list[str]) -> tuple[list[str], list[str]]:
    questions = [q.strip() for q in questions if q and q.strip()][:MAX_QUESTIONS]
    summary = _clean(summary)
    if len(summary) <= MAX_CHUNKS * (MAX_VAR - 20) and all(len(q) <= MAX_VAR for q in questions):
        return _chunk(summary), [q[:MAX_VAR] for q in questions]
    data = _chat_json(_CONDENSE_SYS, json.dumps({"summary": summary, "questions": questions}))
    if data and isinstance(data.get("chunks"), list):
        chunks = [str(c)[:MAX_VAR] for c in data["chunks"]][:MAX_CHUNKS]
        qs = [str(q)[:MAX_VAR] for q in data.get("questions", questions)][:MAX_QUESTIONS]
        return chunks, qs
    return _chunk(summary[: MAX_CHUNKS * (MAX_VAR - 20)]), [q[:MAX_VAR] for q in questions]


def _clean(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[`*_#>|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _chunk(text: str, size: int = MAX_VAR - 20) -> list[str]:
    """Split on sentence boundaries into ≤size pieces, at most MAX_CHUNKS."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(s) > size:
            s = s[: size - 1] + "."
        if len(cur) + len(s) + 1 <= size:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks[:MAX_CHUNKS]


# --------------------------------------------------------------------------- extract

_EXTRACT_SYS = (
    "You read the transcript of a phone call between an AI assistant (role=assistant) and a "
    "software developer (role=user). The assistant reported that a coding task is done and "
    "asked questions. Extract what the DEVELOPER said, as JSON:\n"
    '{"busy": false,\n'
    ' "answers": [{"question_index": 1, "text": "<developer\'s answer in their words>"}],\n'
    ' "instructions": ["<each concrete thing the developer asked the agent to do next>"],\n'
    ' "decisions": [{"text": "<a rule or choice that should hold for future work>", '
    '"topic": "<2-3 word slug>", "applies_to": "<file or area or empty>"}],\n'
    ' "preferences": [{"key": "<e.g. quiet_hours|call_style|language>", "value": "<value>"}]}\n'
    "Rules: instructions are imperative and self-contained (an agent must be able to act on "
    "each one alone). A decision is something to remember (e.g. 'keep /login backward "
    "compatible'), not a one-off instruction. Quiet hours like 'don't call me before 9' "
    "become preferences with key quiet_hours and value 'HH:MM-HH:MM' (24h). If the developer "
    "said they are busy and nothing else, set busy=true. Never invent content."
)


def extract(transcript: list[dict[str, Any]], question_ids: list[str]) -> Extraction:
    """Structured facts from a finished call. LLM first, heuristics as fallback."""
    user_turns = [t for t in transcript if t.get("role") == "user" and t.get("content")]
    if not user_turns:
        return Extraction(busy=True)
    data = _chat_json(
        _EXTRACT_SYS,
        json.dumps({"questions": [f"{i+1}: {qid}" for i, qid in enumerate(question_ids)],
                    "transcript": [{"role": t["role"], "text": t["content"]} for t in transcript]},
                   ensure_ascii=False),
    )
    if data:
        ex = Extraction(busy=bool(data.get("busy")))
        for a in data.get("answers", []) or []:
            try:
                idx = int(a.get("question_index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(question_ids) and a.get("text"):
                ex.answers.append({"question_id": question_ids[idx], "text": str(a["text"])})
        ex.instructions = [str(x).strip() for x in data.get("instructions", []) or [] if str(x).strip()]
        for d in data.get("decisions", []) or []:
            if d.get("text"):
                ex.decisions.append({"text": str(d["text"]), "topic": str(d.get("topic", "")),
                                     "applies_to": str(d.get("applies_to", ""))})
        for p in data.get("preferences", []) or []:
            if p.get("key") and p.get("value"):
                ex.preferences.append({"key": str(p["key"]), "value": str(p["value"])})
        return ex
    return _heuristic(transcript, question_ids)


_SKIP = {"skip", "skip.", "no", "no.", "nothing", "that's all", "no, that's all.", "nope"}


def _heuristic(transcript: list[dict[str, Any]], question_ids: list[str]) -> Extraction:
    """No LLM: map user turns by the node they were spoken at (mock + sandbox shape)."""
    ex = Extraction()
    for t in transcript:
        if t.get("role") != "user":
            continue
        node, text = str(t.get("node_id", "")), str(t.get("content", "")).strip()
        if not text or text.lower() in _SKIP:
            continue
        m = re.match(r"ask_q(\d+)", node)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(question_ids):
                ex.answers.append({"question_id": question_ids[idx], "text": text})
            if re.search(r"\b(always|never|keep|must|should|from now on)\b", text, re.I):
                ex.decisions.append({"text": text, "topic": "", "applies_to": ""})
        elif node in ("ask_more", "capture_task", "confirm"):
            ex.instructions.append(text)
        qh = re.search(r"(?:before|after)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
        if qh and re.search(r"\bcall\b", text, re.I):
            h = int(qh.group(1)) % 12 + (12 if (qh.group(3) or "").lower() == "pm" else 0)
            if "before" in text.lower():
                ex.preferences.append({"key": "quiet_hours", "value": f"22:00-{h:02d}:00"})
            else:
                ex.preferences.append({"key": "quiet_hours", "value": f"{h:02d}:00-09:00"})
    return ex
