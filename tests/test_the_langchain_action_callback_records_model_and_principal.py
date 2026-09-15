"""The LangChain callback: every tool and model call becomes one ledger entry, the model version comes
from what LangChain reports, and the principal is stamped on every entry. Controls: a tool call carries
no model; an interrupted run still leaves a row at the error; nothing is inferred when LangChain reports
no model."""
import pytest

pytest.importorskip("langchain_core")
from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger
from inspeximus.integrations.langchain import InspeximusActionCallback


def test_model_and_principal_flow_from_the_callback_into_the_ledger(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    led = ActionLedger(m, actor="support-agent")
    cb = InspeximusActionCallback(led, principal="user:alice")
    cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="r1",
                           invocation_params={"model": "gpt-5-2026-08", "temperature": 0})
    cb.on_llm_end(type("R", (), {"generations": [[type("G", (), {"text": "hi"})()]]})(), run_id="r1")
    cb.on_tool_start({"name": "search"}, "q", run_id="r2")
    cb.on_tool_error(RuntimeError("boom"), run_id="r2")
    cb.on_llm_start({"name": "Ollama", "kwargs": {"model": "qwen2.5:7b"}}, ["p"], run_id="r3")
    cb.on_llm_end(type("R", (), {"generations": [[]]})(), run_id="r3")
    cb.on_llm_start({"name": "Unknown"}, ["p"], run_id="r4")
    cb.on_llm_end(type("R", (), {"generations": [[]]})(), run_id="r4")
    e = led.entries()
    assert [x["action"] for x in e] == ["llm:ChatOpenAI", "tool:search", "llm:Ollama", "llm:Unknown"]
    assert e[0]["model"] == "gpt-5-2026-08" and e[0]["principal"] == "user:alice"
    assert "model" not in e[1] and e[1]["status"] == "error" and e[1]["principal"] == "user:alice"
    assert e[2]["model"] == "qwen2.5:7b"
    assert "model" not in e[3]                                          # not inferred
    assert led.verify() == (True, [])
