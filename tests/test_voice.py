"""Provider-independent voice tests: flow validity, signature, script limits, curator
heuristics, mock transcript shape."""

from __future__ import annotations

import json

from summon.curator import _chunk, condense, extract
from summon.voice.base import MAX_VAR, CallScript
from summon.voice.flows import summon_done_flow, summon_inbox_flow, validate
from summon.voice.mock import build_transcript
from summon.voice.shravan import sign, verify_signature


def test_flows_pass_hard_rules():
    assert validate(summon_done_flow()) == []
    assert validate(summon_inbox_flow()) == []


def test_flow_vars_within_provider_limits():
    s = CallScript("Satvik", "Claude Code", "summon-ai", "Auth refactor",
                   ["x" * 500], ["q" * 300, "second?"])
    fv = s.flow_vars()
    assert len(fv) <= 20
    assert all(len(v) <= MAX_VAR for v in fv.values())
    assert fv["n_q"] == "two"
    assert fv["q3"] == "That's all my questions."
    assert set(fv) == set(summon_done_flow()["variables"])


def test_condense_chunks_long_summary_without_llm():
    text = " ".join(f"Sentence number {i} explains a change." for i in range(40))
    chunks, qs = condense(text, ["Keep it?"])
    assert 1 <= len(chunks) <= 4 and all(len(c) <= MAX_VAR for c in chunks)
    assert qs == ["Keep it?"]
    assert _chunk("short.") == ["short."]


def test_signature_roundtrip_and_tamper():
    body = json.dumps({"id": "evt_1", "type": "call.analyzed"}).encode()
    hdr = sign(body, "secret")
    assert verify_signature(hdr, body, "secret")
    assert not verify_signature(hdr, body + b" ", "secret")
    assert not verify_signature(hdr, body, "other")
    assert not verify_signature("t=1,v1=00", body, "secret")  # stale timestamp


def test_heuristic_extraction_from_mock_transcript():
    s = CallScript("Satvik", "Claude Code", "demo", "Auth refactor", ["Moved login."],
                   ["Keep the old /login route?", "Run the migration now?"])
    t = build_transcript(s, ["Yes, always keep it backward compatible.", "Skip."],
                         "Add a deprecation header and don't call me before 9 am.")
    ex = extract(t, ["task1:q1", "task1:q2"])
    assert ex.answers == [{"question_id": "task1:q1",
                           "text": "Yes, always keep it backward compatible."}]
    assert ex.instructions == ["Add a deprecation header and don't call me before 9 am."]
    assert ex.decisions and "backward" in ex.decisions[0]["text"]
    assert ex.preferences == [{"key": "quiet_hours", "value": "22:00-09:00"}]
    assert all(set(x) == {"role", "content", "ts", "node_id"} for x in t)
