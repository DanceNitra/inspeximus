"""The ledger keeps a salted digest of what a model or tool was given and what came back, never the
content. `matches(seq, inputs=, output=)` lets the operator who kept the transcript check it against
the entry. Controls: one changed character fails; a side the caller did not pass is None, not False;
the LangChain chat shape carries roles and tool calls, so the same text under different roles is a
different context; the check needs the salt, so a ledger opened without its salt file cannot match."""
import json
import os
import re

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger


def _ledger(tmp_path, **kw):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    return ActionLedger(m, actor="agent", **kw)


def test_the_kept_transcript_matches_and_one_changed_character_does_not(tmp_path):
    led = _ledger(tmp_path)
    prompt = [{"role": "system", "content": "You are support."},
              {"role": "human", "content": "refund order 4471"}]
    e = led.record("llm:chat", inputs=prompt, output=["Refund issued."])
    assert "inputs" not in e, "content-free by default"
    ok = led.matches(e["seq"], inputs=prompt, output=["Refund issued."])
    assert ok == {"seq": e["seq"], "action": "llm:chat", "inputs": True, "output": True}
    edited = [dict(prompt[0]), {"role": "human", "content": "refund order 4472"}]
    assert led.matches(e["seq"], inputs=edited)["inputs"] is False
    assert led.matches(e["seq"], output=["Refund issued"])["output"] is False


def test_a_side_not_passed_or_not_digested_is_none_not_false(tmp_path):
    led = _ledger(tmp_path)
    e = led.record("tool:search", inputs={"q": "x"})                 # no output
    r = led.matches(e["seq"], inputs={"q": "x"})
    assert r["inputs"] is True and r["output"] is None
    r2 = led.matches(e["seq"], output="anything")
    assert r2["inputs"] is None and r2["output"] is None, "the entry has no output digest to compare"


def test_matches_applies_the_ledgers_redaction_before_digesting(tmp_path):
    def redact(v):
        return json.loads(re.sub(r"\+\d+", "[phone]", json.dumps(v)))
    led = _ledger(tmp_path, redact=redact)
    e = led.record("tool:sms", inputs={"to": "+100", "text": "hi"})
    assert led.matches(e["seq"], inputs={"to": "+100", "text": "hi"})["inputs"] is True
    assert led.matches(e["seq"], inputs={"to": "+200", "text": "hi"})["inputs"] is True, \
        "the redacted forms are the same, so both match: the digest never held the number"
    assert led.matches(e["seq"], inputs={"to": "+100", "text": "hello"})["inputs"] is False


def test_without_the_salt_file_the_transcript_cannot_be_matched(tmp_path):
    led = _ledger(tmp_path)
    e = led.record("llm:chat", inputs=["p"])
    os.remove(led.salt_path)
    m2 = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    led2 = ActionLedger(m2, actor="agent")
    assert led2.matches(e["seq"], inputs=["p"])["inputs"] is False, \
        "a fresh salt is generated, the digests no longer line up: the salt is the key to matching"


def test_the_langchain_shape_keeps_roles_and_tool_calls(tmp_path):
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from inspeximus.integrations.langchain import InspeximusActionCallback, context_messages
    led = _ledger(tmp_path)
    cb = InspeximusActionCallback(led)
    msgs = [[SystemMessage(content="You are support."), HumanMessage(content="refund 4471"),
             AIMessage(content="", tool_calls=[{"name": "refund", "args": {"order": 4471}, "id": "c1"}]),
             ToolMessage(content="done", tool_call_id="c1")]]
    cb.on_chat_model_start({"name": "ChatX"}, msgs, run_id="r1")
    cb.on_llm_end(type("R", (), {"generations": [[type("G", (), {"text": "Refunded."})()]]})(), run_id="r1")
    seq = led.entries()[-1]["seq"]
    assert led.matches(seq, inputs=context_messages(msgs), output=[["Refunded."]]) == \
        {"seq": seq, "action": "llm:ChatX", "inputs": True, "output": True}
    # same text, roles swapped: a different context, so it must not match
    swapped = [[HumanMessage(content="You are support."), SystemMessage(content="refund 4471"),
                AIMessage(content="", tool_calls=[{"name": "refund", "args": {"order": 4471}, "id": "c1"}]),
                ToolMessage(content="done", tool_call_id="c1")]]
    assert led.matches(seq, inputs=context_messages(swapped))["inputs"] is False
    # same text, the tool call removed: also a different context
    no_call = [[SystemMessage(content="You are support."), HumanMessage(content="refund 4471"),
                AIMessage(content=""), ToolMessage(content="done", tool_call_id="c1")]]
    assert led.matches(seq, inputs=context_messages(no_call))["inputs"] is False
    shape = context_messages(msgs)[0]
    assert shape[2]["tool_calls"] == [{"name": "refund", "args": {"order": 4471}, "id": "c1"}]
    assert shape[3]["tool_call_id"] == "c1" and shape[0]["role"] == "system"
