"""The OpenAI Agents SDK example (examples/integrations/openai_agents): every tool call of a run is one
signed entry in the action ledger with what it was based on, and the verifier fails when the ledger
changes by one byte.

Everything runs on the SDK's own stub model, `agents.testing.ScriptedModel`, with OPENAI_API_KEY removed
from the environment: no key, no network. The example pins openai-agents==0.22.3; these tests run
against whatever the environment installed, and the pin itself is checked for agreement between the
README and requirements.txt, so a move of the pin is one reviewed edit in two places.

The negative controls carry the weight. A verifier that passes its own fixture shows nothing, so this
one is run against every single-bit flip of the ledger file, a whitespace swap no JSON parser can see, an
entry forged and re-hashed without the key, a ledger re-signed with another key, a dropped tail entry and
an edited transcript. Each must fail, and the untouched copy beside each must pass.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "integrations", "openai_agents")
PINNED = "0.22.3"
LEDGER = "memory.json.actions.json"


def _sdk():
    """The example's modules. The guard sits here, not at module level, so a base install without the
    SDK shows each skipped test by name (see test_skip_census.py)."""
    pytest.importorskip("agents", reason=f"needs openai-agents (the example pins {PINNED})")
    pytest.importorskip("cryptography", reason="the store's receipts and the ledger are Ed25519-signed")
    if EXAMPLE not in sys.path:
        sys.path.insert(0, EXAMPLE)
    import agent
    import ledger_hooks
    import verify_ledger
    return agent, ledger_hooks, verify_ledger


def _env(keys):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}   # no key, no base URL
    env.update(INSPEXIMUS_KEY_HOME=str(keys), PYTHONIOENCODING="utf-8",
               PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return env


def _script(name, cwd, keys, *args):
    return subprocess.run([sys.executable, os.path.join(EXAMPLE, name), *args], cwd=str(cwd), env=_env(keys),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """One stub run of agent.py, the way a reader runs it. Tests copy its data directory before editing."""
    _sdk()
    base = tmp_path_factory.mktemp("oai_example")
    keys = base / "keys"
    r = _script("agent.py", base, keys)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    data = base / "agent_data"
    pubkey = json.loads((data / LEDGER).read_text(encoding="utf-8"))[0]["pubkey"]
    return {"base": base, "keys": keys, "data": data, "pubkey": pubkey, "stdout": r.stdout}


def _copy(demo, tmp_path):
    """A copy of the run elsewhere. The receipt key is named by the store's absolute path, so a copy is
    verified against the pinned public key, the way an auditor holding only the files would."""
    dst = tmp_path / "agent_data"
    shutil.copytree(demo["data"], dst)
    return dst


def _problems(demo, data):
    _, _, vl = _sdk()
    return vl.verify(str(data), pubkey=demo["pubkey"])[0]


def _rows(data):
    """The store's records, read without opening a store handle. `memory.json` is a SQLite file: the name
    is the store's path, and the row format is the default for a new store."""
    con = sqlite3.connect(f"file:{data / 'memory.json'}?mode=ro", uri=True)
    try:
        return [json.loads(doc) for (doc,) in con.execute("SELECT doc FROM records ORDER BY ord")]
    finally:
        con.close()


def _write_canonical(path, entries):
    path.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ the run and what it recorded
def test_the_stub_run_needs_no_key_and_the_verifier_passes(demo):
    r = _script("verify_ledger.py", demo["base"], demo["keys"], "--dir", "agent_data")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("OK "), r.stdout
    assert "5 tool calls in the transcript, 5 ledger entries" in r.stdout, r.stdout


def test_every_tool_call_is_one_signed_entry_with_what_it_was_based_on(demo):
    agent, _, _ = _sdk()
    entries = json.loads((demo["data"] / LEDGER).read_text(encoding="utf-8"))
    assert [e["action"] for e in entries] == ["tool:recall_memory", "tool:send_invoice", "tool:remember_fact",
                                              "tool:recall_memory", "tool:send_invoice"]
    assert {e["pubkey"] for e in entries} == {demo["pubkey"]} and all(e.get("sig") for e in entries)
    for e in entries:
        ms, meta = e["memory_state"], e["meta"]
        assert ms["digest"] and ms["last_receipt"], e["seq"]
        assert meta["call_id"] and e["session"] == agent.SESSION_ID and e["principal"] == agent.PRINCIPAL
        assert e["model"] == "ScriptedModel"
    # a correction between two actions: two memory states, and each send names the fact it was sent on
    facts = {r["id"]: r for r in _rows(demo["data"]) if r.get("key") == agent.EMAIL_KEY}
    old = next(i for i, r in facts.items() if r["object"] == "ana@old-mail.example")
    new = next(i for i, r in facts.items() if r["object"] == "ana@new-mail.example")
    assert facts[old]["status"] == "superseded" and facts[new]["status"] == "active"
    first, second = entries[1], entries[4]
    assert first["memory_state"]["recalled"] == [old], first["memory_state"]
    assert second["memory_state"]["recalled"] == [new], second["memory_state"]
    assert first["memory_state"]["digest"] != second["memory_state"]["digest"]
    # and each names the recall whose output the model had in front of it
    assert {"seq": 0, "hash": entries[0]["hash"]} in first["meta"]["based_on"]
    assert {"seq": 3, "hash": entries[3]["hash"]} in second["meta"]["based_on"]
    sent = [json.loads(x) for x in (demo["data"] / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [s["to"] for s in sent] == ["ana@old-mail.example", "ana@new-mail.example"]
    assert "(superseded now)" in demo["stdout"] and "(active now)" in demo["stdout"]


def test_the_verifier_writes_nothing(demo, tmp_path):
    data = _copy(demo, tmp_path)
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    assert _problems(demo, data) == []
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before


# ------------------------------------------------------------------ negative controls
def test_one_byte_edit_fails_the_verifier_and_restoring_it_passes(demo, tmp_path):
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    raw = p.read_bytes()
    at = raw.index(b'"tool:send_invoice"') + len('"tool:send_invoic')
    p.write_bytes(raw[:at] + b"f" + raw[at + 1:])                     # send_invoice -> send_invoicf
    r = _script("verify_ledger.py", tmp_path, demo["keys"], "--dir", str(data), "--pubkey", demo["pubkey"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("FAIL") and "seq 1: hash does not match" in r.stdout, r.stdout
    p.write_bytes(raw)
    r = _script("verify_ledger.py", tmp_path, demo["keys"], "--dir", str(data), "--pubkey", demo["pubkey"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_single_bit_flip_of_the_ledger_fails(demo, tmp_path):
    """Exhaustive, not sampled: bit 0 of every byte, one at a time, the verifier run after each."""
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    raw = p.read_bytes()
    missed = []
    for i in range(len(raw)):
        b = bytearray(raw)
        b[i] ^= 0x01
        p.write_bytes(bytes(b))
        if not _problems(demo, data):
            missed.append(i)
    p.write_bytes(raw)
    assert _problems(demo, data) == [], "the restored file must verify, or every failure above is noise"
    assert len(raw) > 4000 and missed == [], [raw[max(0, i - 20):i + 20] for i in missed[:5]]


def test_a_whitespace_swap_no_parser_can_see_still_fails(demo, tmp_path):
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    raw = p.read_bytes()
    at = raw.index(b'\n  "v"')                   # indentation between two tokens, outside every value
    p.write_bytes(raw[:at + 1] + b"\t" + raw[at + 2:])
    assert json.loads(p.read_bytes()) == json.loads(raw)                # the same content, parsed
    problems = _problems(demo, data)
    assert problems and "differs from the bytes ActionLedger writes" in problems[0], problems


def test_an_entry_forged_and_rehashed_without_the_key_fails(demo, tmp_path):
    """The attacker who can edit the file but does not hold the key rewrites what the first invoice was
    based on, recomputes the hash and relinks the chain. Only the signature is left to catch it."""
    from inspeximus.actions import _entry_hash
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    entries = json.loads(p.read_text(encoding="utf-8"))
    entries[1]["memory_state"]["recalled"] = entries[4]["memory_state"]["recalled"]
    for i in range(1, len(entries)):
        entries[i]["prev"] = entries[i - 1]["hash"]
        entries[i]["hash"] = _entry_hash(entries[i])
    _write_canonical(p, entries)
    problems = _problems(demo, data)
    assert any("seq 1: signature does not verify" in x for x in problems), problems


def test_a_ledger_resigned_with_another_key_fails_against_the_pinned_one(demo, tmp_path):
    from inspeximus.actions import _sign
    from inspeximus.core import new_receipt_keypair
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    entries = json.loads(p.read_text(encoding="utf-8"))
    sk, _ = new_receipt_keypair()
    for e in entries:
        e["sig"], e["pubkey"] = _sign(sk, e["hash"])
    _write_canonical(p, entries)
    problems = _problems(demo, data)
    assert any("signed by an unexpected key" in x for x in problems), problems


def test_a_dropped_last_entry_fails_on_the_transcript(demo, tmp_path):
    """A prefix of a valid chain is a valid chain, so dropping the tail passes every hash check. The
    transcript, held in the receipted store, still has the call."""
    data = _copy(demo, tmp_path)
    p = data / LEDGER
    entries = json.loads(p.read_text(encoding="utf-8"))
    dropped = entries.pop()
    _write_canonical(p, entries)
    problems = _problems(demo, data)
    assert any(dropped["meta"]["call_id"] in x and "has 0 ledger entries" in x for x in problems), problems


def test_an_edited_transcript_fails(demo, tmp_path):
    """The first send_invoice call, as the session stored it, now names another address."""
    data = _copy(demo, tmp_path)
    con = sqlite3.connect(str(data / "memory.json"))
    try:
        for rid, doc in con.execute("SELECT id, doc FROM records ORDER BY ord").fetchall():
            item = (json.loads(doc).get("meta") or {}).get("item") or {}
            if item.get("name") == "send_invoice" and "ana@old-mail.example" in item.get("arguments", ""):
                con.execute("UPDATE records SET doc = ? WHERE id = ?",
                            (doc.replace("ana@old-mail.example", "ana@evil.example"), rid))
                break
        else:
            pytest.fail("the first send_invoice call is not in the stored transcript")
        con.commit()
    finally:
        con.close()
    problems = _problems(demo, data)
    assert any(x.startswith("memory: ") for x in problems), problems
    assert any("digests do not match transcript call" in x for x in problems), problems


# ------------------------------------------------------------------ what the SDK's hooks do not show
@pytest.fixture
def sdk():
    """The guard, as a fixture: these tests import from `agents` in their own bodies, which must not run
    where the SDK is absent."""
    return _sdk()


def _run(tmp_path, monkeypatch, steps, tools=(), handoffs=()):
    agent_mod, lh, vl = _sdk()
    from agents import Agent, RunConfig, Runner
    from agents.testing import ScriptedModel
    from inspeximus.integrations.openai_agents import InspeximusSession
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    d = str(tmp_path / "d")
    store, led = agent_mod.open_memory(d)
    hooks = lh.InspeximusLedgerHooks(led, session="s")
    ag = Agent(name="a", model=ScriptedModel(steps), tools=[t(hooks) for t in tools], handoffs=list(handoffs))
    result, raised = None, None
    try:
        result = asyncio.run(Runner.run(ag, "go", session=InspeximusSession("s", store=store), hooks=hooks,
                                        run_config=RunConfig(tracing_disabled=True)))
    except Exception as e:  # noqa: BLE001 - one case below expects the run to raise
        raised = e
    finally:
        hooks.flush(result)
    store.flush()
    return led.entries(), vl.verify(d, "s")[0], raised


def _tool(fn, **kw):
    from agents import function_tool
    return lambda hooks: function_tool(fn, **{k: (hooks.tool_error if v == "hooks" else v) for k, v in kw.items()})


def _boom(x: int) -> str:
    """Always fails."""
    raise RuntimeError("disk full")


def _echo(x: int) -> str:
    """Echo."""
    return str(x)


def test_a_raised_tool_is_an_error_entry_not_a_success(sdk, tmp_path, monkeypatch):
    """The SDK has no on_tool_error: its default handler hands on_tool_end the error text as a result."""
    from agents.testing import assistant_message, function_call
    entries, problems, raised = _run(tmp_path, monkeypatch, [[function_call("_boom", {"x": 1}, call_id="c1")],
                                                             [assistant_message("ok")]],
                                     tools=[_tool(_boom, failure_error_function="hooks")])
    assert raised is None and problems == []
    assert [(e["action"], e["status"]) for e in entries] == [("tool:_boom", "error")]
    assert entries[0]["error"] == "RuntimeError: disk full"


def test_an_exception_that_escapes_the_run_is_still_an_entry(sdk, tmp_path, monkeypatch):
    """failure_error_function=None: on_tool_end never fires, the run raises, and the SDK saves nothing
    of the turn to the session. flush() leaves the only record that the tool ran."""
    from agents.testing import assistant_message, function_call
    entries, problems, raised = _run(tmp_path, monkeypatch, [[function_call("_boom", {"x": 1}, call_id="c1")],
                                                             [assistant_message("ok")]],
                                     tools=[_tool(_boom, failure_error_function=None)])
    assert raised is not None and problems == []
    assert [(e["action"], e["status"], e["meta"].get("flushed")) for e in entries] == [("tool:_boom", "error", True)]


def test_a_call_a_guardrail_refused_is_recorded_as_not_run(sdk, tmp_path, monkeypatch):
    from agents.testing import assistant_message, function_call
    from agents.tool_guardrails import ToolGuardrailFunctionOutput, tool_input_guardrail

    @tool_input_guardrail
    def refuse(data):
        return ToolGuardrailFunctionOutput.reject_content("refused by policy")
    entries, problems, _ = _run(tmp_path, monkeypatch, [[function_call("_echo", {"x": 9}, call_id="c1")],
                                                        [assistant_message("ok")]],
                                tools=[_tool(_echo, tool_input_guardrails=[refuse])])
    assert problems == [], problems                    # the refusal is the output digest, and it matches
    assert [(e["action"], e["status"]) for e in entries] == [("tool:_echo", "not_run")]


@pytest.mark.parametrize("approve", [False, True], ids=["rejected", "approved"])
def test_a_call_waiting_on_approval_stays_pending_then_lands_once(sdk, tmp_path, monkeypatch, approve):
    """The run stops at the interruption; flush(result) must not close the call, because the resumed
    run decides it. Rejected, the SDK answers without running the tool (not_run); approved, it runs."""
    agent_mod, lh, vl = sdk
    from agents import Agent, RunConfig, Runner, function_tool
    from agents.testing import ScriptedModel, assistant_message, function_call
    from inspeximus.integrations.openai_agents import InspeximusSession
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    d = str(tmp_path / "d")
    store, led = agent_mod.open_memory(d)
    hooks = lh.InspeximusLedgerHooks(led, session="s")
    ag = Agent(name="a", tools=[function_tool(_echo, needs_approval=True)],
               model=ScriptedModel([[function_call("_echo", {"x": 9}, call_id="c1")], [assistant_message("ok")]]))
    session, cfg = InspeximusSession("s", store=store), RunConfig(tracing_disabled=True)

    async def go():
        first = await Runner.run(ag, "go", session=session, hooks=hooks, run_config=cfg)
        hooks.flush(first)
        pending = len(led.entries())
        state = first.to_state()
        for item in first.interruptions:
            (state.approve if approve else state.reject)(item)
        hooks.flush(await Runner.run(ag, state, session=session, hooks=hooks, run_config=cfg))
        return first.interruptions, pending
    interruptions, pending = asyncio.run(go())
    store.flush()
    assert len(interruptions) == 1 and pending == 0
    assert [(e["action"], e["status"]) for e in led.entries()] == [("tool:_echo", "ok" if approve else "not_run")]
    assert vl.verify(d, "s")[0] == []


def test_the_verifier_runs_inside_an_event_loop(sdk, demo, tmp_path):
    """An agent app verifying its own ledger calls this from async code, where asyncio.run() refuses."""
    _, _, vl = sdk
    data = _copy(demo, tmp_path)

    async def inside():
        return vl.verify(str(data), pubkey=demo["pubkey"])[0]
    assert asyncio.run(inside()) == []


def test_a_hosted_tool_call_is_recorded_from_the_model_response(sdk, tmp_path, monkeypatch):
    from agents.testing import assistant_message
    from openai.types.responses import ResponseFunctionWebSearch
    from openai.types.responses.response_function_web_search import ActionSearch
    ws = ResponseFunctionWebSearch(id="ws_1", status="completed", type="web_search_call",
                                   action=ActionSearch(type="search", query="vat rate"))
    entries, problems, _ = _run(tmp_path, monkeypatch, [[ws, assistant_message("found it")]])
    assert problems == [], problems
    assert [(e["action"], e["meta"]["executed_by"]) for e in entries] == [("hosted:web_search_call", "provider")]


def test_parallel_calls_are_one_entry_each_on_the_same_memory(sdk, tmp_path, monkeypatch):
    from agents.testing import assistant_message, function_call
    entries, problems, _ = _run(tmp_path, monkeypatch,
                                [[function_call("_echo", {"x": 1}, call_id="c1"),
                                  function_call("_echo", {"x": 2}, call_id="c2")], [assistant_message("ok")]],
                                tools=[_tool(_echo)])
    assert problems == [], problems
    assert sorted(e["meta"]["call_id"] for e in entries) == ["c1", "c2"]
    assert len({e["memory_state"]["digest"] for e in entries}) == 1


def test_a_handoff_is_recorded_with_the_transfer_the_model_saw(sdk, tmp_path, monkeypatch):
    from agents import Agent
    from agents.testing import ScriptedModel, assistant_message, function_call
    other = Agent(name="specialist", model=ScriptedModel([[assistant_message("specialist here")]]))
    entries, problems, _ = _run(tmp_path, monkeypatch,
                                [[function_call("transfer_to_specialist", {}, call_id="h1")]], handoffs=[other])
    assert problems == [], problems
    assert [(e["action"], e["status"], e["meta"]["handoff_to"]) for e in entries] == \
        [("handoff:transfer_to_specialist", "ok", "specialist")]


# ------------------------------------------------------------------ the pin
def test_the_sdk_pin_is_one_version_in_the_readme_and_requirements():
    """No SDK needed: this is about what a reader is told to install."""
    req = open(os.path.join(EXAMPLE, "requirements.txt"), encoding="utf-8").read()
    readme = open(os.path.join(EXAMPLE, "README.md"), encoding="utf-8").read()
    assert re.findall(r"^openai-agents==([\d.]+)$", req, re.M) == [PINNED], req
    named = set(re.findall(r"openai-agents==([\d.]+)", readme))
    assert named == {PINNED}, f"README names {sorted(named)}, requirements.txt pins {PINNED}"
