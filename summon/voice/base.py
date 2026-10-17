"""Voice provider interface. A backend only places calls; results always come back
through the API (webhook, poll, or a mock reply), never through the backend itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_VAR = 200     # Shravan flow_vars: ≤20 keys, ≤200 chars each
MAX_CHUNKS = 4
MAX_QUESTIONS = 3


@dataclass
class CallScript:
    """Everything the voice agent needs to say, already cut to provider limits."""

    developer_name: str
    bot: str                      # "Claude Code" / "Codex"
    project: str
    task_title: str
    summary_chunks: list[str]     # ≤4 × ≤200 chars
    questions: list[str]          # ≤3 × ≤200 chars
    direction: str = "outbound"

    def flow_vars(self) -> dict[str, str]:
        chunks = (self.summary_chunks + [""] * MAX_CHUNKS)[:MAX_CHUNKS]
        qs = (self.questions + [""] * MAX_QUESTIONS)[:MAX_QUESTIONS]
        n = len([q for q in self.questions if q])
        fv = {
            "bot": self.bot[:MAX_VAR],
            "project": self.project[:MAX_VAR],
            "task_title": self.task_title[:MAX_VAR],
            "n_q": {0: "no", 1: "one", 2: "two", 3: "three"}.get(n, str(n)),
        }
        for i, c in enumerate(chunks, 1):
            fv[f"s{i}"] = c[:MAX_VAR]
        for i, q in enumerate(qs, 1):
            fv[f"q{i}"] = (q or "That's all my questions.")[:MAX_VAR]
        return fv

    def spoken_lines(self) -> list[tuple[str, str]]:
        """(node_id, text) in order; used by the mock backend and the UI."""
        fv = self.flow_vars()
        greet = (f"Hi {self.developer_name}, this is Summon, an AI assistant calling "
                 f"for {fv['bot']}. {fv['bot']} has finished a task on {fv['project']}. "
                 f"{' '.join(c for c in self.summary_chunks if c)} "
                 f"I have {fv['n_q']} quick questions, do you have a minute?")
        lines = [("greet", greet)]
        for i, q in enumerate(self.questions, 1):
            if q:
                lines.append((f"ask_q{i}", q))
        lines.append(("ask_more", f"Anything else you want {fv['bot']} to do next?"))
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "developer_name": self.developer_name, "bot": self.bot, "project": self.project,
            "task_title": self.task_title, "summary_chunks": self.summary_chunks,
            "questions": self.questions, "direction": self.direction,
        }


@dataclass
class PlacedCall:
    provider_call_id: str
    status: str = "placed"        # placed | simulated
    detail: dict[str, Any] = field(default_factory=dict)


class VoiceBackend(Protocol):
    name: str

    async def place_call(self, call_id: str, to_phone: str, script: CallScript) -> PlacedCall: ...

    async def fetch_result(self, provider_call_id: str) -> dict[str, Any] | None:
        """Poll fallback: return a call resource with transcript when finished, else None."""
        ...
