"""Tests for the agno action-receipts example. No API key and no network: the model is ScriptedModel.

    pip install -r examples/integrations/agno_actions/requirements.txt
    pip install -e .
    python -m pytest examples/integrations/agno_actions -q

What they hold the example to:
  - every tool call agno runs, sync, `async def` under arun(), streamed, a Toolkit method, or answered
    from agno's tool cache, is exactly one ledger entry, and a tool that raises is one entry with
    status "error";
  - each entry names the fact the call was based on, and the fact behind the first migration is the
    one the later correction superseded;
  - verify.py fails on every single-byte edit of the ledger, the store's receipt file, the
    transcript, the salt and the pinned key, with two kinds of edit per byte, and on a one-byte edit
    of any record's text, key, id or status in the store. The restored files verify again, which is
    the control that the sweep is not failing on something else;
  - an agent run without the hook fails verify.py.
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
from importlib.metadata import version
from pathlib import Path

import pytest

pytest.importorskip("agno", reason="pip install -r examples/integrations/agno_actions/requirements.txt")
pytest.importorskip("cryptography", reason="the ledger is signed; needs cryptography")

from agno.agent import Agent  # noqa: E402
from agno.tools import Toolkit, tool  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.actions import ActionLedger  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

import run as example  # noqa: E402
from ledger_hook import ledger_hook  # noqa: E402
from stub_model import ScriptedModel  # noqa: E402
from verify import verify_run  # noqa: E402

LEDGER = example.STORE + ".actions.json"
SWEPT = [LEDGER, example.STORE + ".receipts.json", example.TRANSCRIPT, LEDGER + ".salt", example.PUBKEY]


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """The signing keys live outside every run directory, as receipt_key_for requires."""
    home = tmp_path_factory.mktemp("keys")
    old = os.environ.get("INSPEXIMUS_KEY_HOME")
    os.environ["INSPEXIMUS_KEY_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("INSPEXIMUS_KEY_HOME", None)
    else:
        os.environ["INSPEXIMUS_KEY_HOME"] = old


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory, keys):
    out = tmp_path_factory.mktemp("agno_run") / "run"
    example.main(["--out", str(out)])
    return out


def _copy(run_dir, tmp_path) -> Path:
    d = tmp_path / "run"
    shutil.copytree(run_dir, d)
    return d


def _open(d: Path):
    store = Inspeximus(str(d / example.STORE), receipts=True)
    return store, ActionLedger(store)


def _rows(d: Path) -> list:
    return json.loads((d / example.TRANSCRIPT).read_text(encoding="utf-8"))


def _tool_entries(ledger):
    return [e for e in ledger.entries() if e["action"].startswith("tool:")]


# --- the pin -----------------------------------------------------------------------------------------
def test_the_installed_agno_is_the_pinned_one():
    text = (HERE / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^agno==(\S+)$", text, re.M)
    assert pinned, "requirements.txt does not pin agno to one version"
    assert version("agno") == pinned.group(1), (
        f"these tests ran against agno {version('agno')}, the example pins {pinned.group(1)}")


# --- every tool call, once ---------------------------------------------------------------------------
def test_the_run_verifies(run_dir):
    assert verify_run(run_dir) == []


def test_every_tool_call_agno_ran_is_one_signed_entry(run_dir):
    store, ledger = _open(run_dir)
    rows = _rows(run_dir)
    entries = _tool_entries(ledger)
    assert [r["tool_name"] for r in rows] == ["recall_memory", "run_migration"] * 2
    assert [e["meta"]["tool_call_id"] for e in entries] == [r["tool_call_id"] for r in rows]
    pub = (run_dir / example.PUBKEY).read_text(encoding="utf-8").strip()
    for e, r in zip(entries, rows):
        assert e["action"] == "tool:" + r["tool_name"] and e["status"] == "ok"
        assert e["pubkey"] == pub and e["sig"]
        assert e["model"] == "scripted-stub" and e["session"] == r["session_id"]
        assert e["principal"] == r["user_id"] == "ops@example.com"
        assert "inputs" not in e and "output" not in e, "the ledger keeps digests, not content"
        m = ledger.matches(e["seq"], inputs=r["tool_args"], output=r["result"])
        assert m["inputs"] is True and m["output"] is True


def test_each_action_names_the_fact_it_was_based_on(run_dir):
    store, ledger = _open(run_dir)
    by_call = {r["tool_call_id"]: r for r in _rows(run_dir)}
    migrations = [e for e in _tool_entries(ledger) if e["action"] == "tool:run_migration"]
    assert len(migrations) == 2
    for e in migrations:
        host = by_call[e["meta"]["tool_call_id"]]["tool_args"]["database"]
        texts = [store.provenance(id=rid)["current"]["text"] for rid in e["memory_state"]["recalled"]]
        assert texts == [f"The staging database is {host}"], "the recalled fact is the one the call used"
    first, second = migrations
    assert first["memory_state"]["digest"] != second["memory_state"]["digest"]
    assert by_call[first["meta"]["tool_call_id"]]["tool_args"] == {"database": "db-3.internal"}
    assert by_call[second["meta"]["tool_call_id"]]["tool_args"] == {"database": "db-7.internal"}
    # what_it_knew: the fact behind the first migration was current then and was superseded after it
    knew = ledger.what_it_knew(first["seq"])["recalled_now"][0]
    assert knew["current"]["status"] == "superseded"
    retired = next(t for t in knew["timeline"] if t["id"] == knew["current"]["id"])
    assert first["ts"] < retired["invalidated_at"]


def test_the_recall_itself_is_recorded_and_not_credited_twice(run_dir):
    """The recall_memory call reports the window before it, not its own result; the result is what the
    next action is based on."""
    _, ledger = _open(run_dir)
    recalls = [e for e in _tool_entries(ledger) if e["action"] == "tool:recall_memory"]
    assert [e["memory_state"]["recalled"] for e in recalls] == [[], []]


def _agent(store, ledger, tools):
    return Agent(model=ScriptedModel(), tools=tools, tool_hooks=[ledger_hook(ledger)],
                 user_id="ops@example.com", session_id="test", telemetry=False)


def _fixture_store(tmp_path, keys):
    store = example.open_store(tmp_path)
    store.remember("The staging database is db-7.internal", key="staging-db")
    return store, ActionLedger(store, actor="test")


def _tools(store, calls=None):
    def recall_memory(query: str) -> str:
        """Return what the agent remembers."""
        return "\n".join(h["text"] for h in store.recall(query, k=3))

    def run_migration(database: str) -> str:
        """Apply the migration."""
        if calls is not None:
            calls.append(database)
        return f"applied on {database}"
    return recall_memory, run_migration


def test_an_async_tool_under_arun_records_the_awaited_result(tmp_path, keys):
    """Under arun() agno runs a sync tool through the sync hook chain and an `async def` tool through
    the async one, where the hook's function_call is a coroutine function. Recording its return value
    there would digest a coroutine object and close the entry before the tool ran. Both kinds here."""
    store, ledger = _fixture_store(tmp_path, keys)
    ran = []

    async def run_migration(database: str) -> str:
        """Apply the migration."""
        await asyncio.sleep(0)
        ran.append(len(ledger))                   # entries already written when the tool body runs
        return f"applied on {database}"

    out = asyncio.run(_agent(store, ledger, [_tools(store)[0], run_migration]).arun(example.REQUEST))
    entries = _tool_entries(ledger)
    assert [e["action"] for e in entries] == ["tool:recall_memory", "tool:run_migration"]
    for e, t in zip(entries, out.tools):
        assert ledger.matches(e["seq"], inputs=t.tool_args, output=t.result) == {
            "seq": e["seq"], "action": e["action"], "inputs": True, "output": True}
    assert ran == [1], "the entry for the async call was written after its body ran, not before"
    assert entries[1]["started"] <= entries[1]["ts"]
    assert entries[1]["memory_state"]["recalled"], "the async call still says what it was based on"


def test_a_streamed_run_is_recorded_the_same_way(tmp_path, keys):
    store, ledger = _fixture_store(tmp_path, keys)
    done = [ev.tool for ev in _agent(store, ledger, list(_tools(store))).run(example.REQUEST, stream=True,
                                                                              stream_events=True)
            if getattr(ev, "event", None) == "ToolCallCompleted"]
    assert len(done) == 2
    entries = _tool_entries(ledger)
    assert [e["meta"]["tool_call_id"] for e in entries] == [t.tool_call_id for t in done]
    for e, t in zip(entries, done):
        m = ledger.matches(e["seq"], inputs=t.tool_args, output=t.result)
        assert m["inputs"] is True and m["output"] is True


def test_a_call_answered_from_agnos_tool_cache_is_still_recorded(tmp_path, keys):
    store, ledger = _fixture_store(tmp_path, keys)
    ran = []

    @tool(cache_results=True, cache_dir=str(tmp_path / "cache"))
    def run_migration(database: str) -> str:
        """Apply the migration."""
        ran.append(database)
        return f"applied on {database}"

    agent = _agent(store, ledger, [_tools(store)[0], run_migration])
    agent.run(example.REQUEST)
    agent.run(example.REQUEST)
    assert ran == ["db-7.internal"], "the second call was served from agno's cache"
    assert [e["action"] for e in _tool_entries(ledger)].count("tool:run_migration") == 2


def test_a_toolkit_method_is_recorded_too(tmp_path, keys):
    """agno sets the agent's tool_hooks on every function of a Toolkit as well."""
    store, ledger = _fixture_store(tmp_path, keys)

    class OpsTools(Toolkit):
        def __init__(self):
            super().__init__(name="ops", tools=[self.run_migration])

        def run_migration(self, database: str) -> str:
            """Apply the migration."""
            return f"applied on {database}"

    out = _agent(store, ledger, [_tools(store)[0], OpsTools()]).run(example.REQUEST)
    entries = _tool_entries(ledger)
    assert [e["action"] for e in entries] == ["tool:recall_memory", "tool:run_migration"]
    m = ledger.matches(entries[1]["seq"], inputs=out.tools[1].tool_args, output=out.tools[1].result)
    assert m["inputs"] is True and m["output"] is True


def test_a_tool_that_raises_is_one_entry_with_status_error(tmp_path, keys):
    store, ledger = _fixture_store(tmp_path, keys)

    def recall_memory(query: str) -> str:
        """Return what the agent remembers."""
        raise RuntimeError("memory backend unavailable")

    out = _agent(store, ledger, [recall_memory]).run(example.REQUEST)
    (e,) = _tool_entries(ledger)
    assert e["status"] == "error" and "memory backend unavailable" in e["error"]
    assert out.tools[0].tool_call_error, "agno still reports the failure to the model"
    assert ledger.matches(e["seq"], inputs=out.tools[0].tool_args)["inputs"] is True
    assert out.content == "I do not know which staging database to use."


# --- the verify script -------------------------------------------------------------------------------
def _flip(byte: int, how: str) -> int:
    """`xor1` changes the lowest bit. `same_class` swaps the byte for another of its kind, which keeps
    the file valid JSON: a digit for a digit, a hex letter for a hex letter, a space for a tab, a
    newline for a space. That is the edit a careless or deliberate hand makes."""
    if how == "xor1":
        return byte ^ 0x01
    c = chr(byte)
    if c.isdigit():
        return ord(str((int(c) + 1) % 10))
    if c in "abcdef":
        return ord("abcdef"[("abcdef".index(c) + 1) % 6])
    if c == " ":
        return ord("\t")
    if c == "\n":
        return ord(" ")
    if c.isalpha():
        return ord(c.swapcase())
    return byte ^ 0x01


@pytest.mark.parametrize("how", ["xor1", "same_class"])
@pytest.mark.parametrize("name", SWEPT)
def test_every_single_byte_edit_fails_verification(run_dir, tmp_path, name, how):
    d = _copy(run_dir, tmp_path)
    path = d / name
    orig = path.read_bytes()
    assert verify_run(d) == [], "the untouched copy must verify, or the sweep proves nothing"
    missed = []
    for i in range(len(orig)):
        edited = bytearray(orig)
        edited[i] = _flip(orig[i], how)
        path.write_bytes(bytes(edited))
        problems = verify_run(d)
        if not problems:
            missed.append(i)
    path.write_bytes(orig)
    assert verify_run(d) == [], "restored, it verifies again"
    assert len(orig) > 60
    assert not missed, f"{len(missed)} of {len(orig)} one-byte edits of {name} verified clean, at {missed[:10]}"


@pytest.mark.parametrize("field", ["text", "key", "id", "status"])
def test_a_one_byte_edit_of_what_the_store_holds_fails_verification(run_dir, tmp_path, field):
    """The store is SQLite, so most of its bytes are page layout and free space and are not a record.
    The check is over the bytes of each live record's field: an edit there is an edit of what the
    agent knew, and it must fail."""
    d = _copy(run_dir, tmp_path)
    path = d / example.STORE
    orig = path.read_bytes()

    def docs(p):
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return [r[0] for r in con.execute("select doc from records order by ord")]
        finally:
            con.close()

    live = docs(path)
    tried, missed = 0, []
    for doc in live:
        raw = doc.encode("utf-8")
        value = str(json.loads(doc)[field]).encode("utf-8")
        offset = raw.index(f'"{field}": "'.encode("utf-8")) + len(field) + 5    # the value's first byte
        # A superseded row can leave a stale copy of its old doc in a free page. Every copy is tried,
        # and only an edit that changes what SQLite returns counts as an edit of the record.
        for m in re.finditer(re.escape(raw), orig):
            for i in range(m.start() + offset, m.start() + offset + len(value)):
                edited = bytearray(orig)
                edited[i] ^= 0x01
                path.write_bytes(bytes(edited))
                try:
                    changed = docs(path) != live
                except sqlite3.DatabaseError:
                    changed = True
                if not changed:
                    continue
                tried += 1
                if not verify_run(d):
                    missed.append(i)
    path.write_bytes(orig)
    assert verify_run(d) == [], "restored, it verifies again"
    assert tried == sum(len(str(json.loads(doc)[field]).encode("utf-8")) for doc in live), (
        "every byte of the field in every live record was edited once")
    assert not missed, f"{len(missed)} one-byte edits of a record's {field} verified clean"


def test_an_agent_without_the_hook_fails_verification(run_dir, tmp_path, keys):
    """The control for "every tool call": tool calls agno ran that the ledger never saw."""
    d = _copy(run_dir, tmp_path)
    store, _ = _open(d)
    unhooked = Agent(model=ScriptedModel(), tools=list(_tools(store)), user_id="ops@example.com",
                     session_id="staging-maintenance", telemetry=False)
    rows = _rows(d) + example.transcript_rows(unhooked.run(example.REQUEST))
    example.write_canonical(d / example.TRANSCRIPT, rows)
    problems = verify_run(d)
    assert sum("the ledger has no entry for it" in p for p in problems) == 2, problems


def test_a_dropped_transcript_row_fails_verification(run_dir, tmp_path):
    d = _copy(run_dir, tmp_path)
    example.write_canonical(d / example.TRANSCRIPT, _rows(d)[:-1])
    assert any("a tool call agno never reported" in p for p in verify_run(d))


def test_a_changed_argument_fails_even_when_rewritten_cleanly(run_dir, tmp_path):
    """Not a byte flip: the transcript rewritten in its exact canonical form with another database.
    Only the ledger's digest can catch that."""
    d = _copy(run_dir, tmp_path)
    rows = _rows(d)
    rows[1]["tool_args"]["database"] = "db-9.internal"
    example.write_canonical(d / example.TRANSCRIPT, rows)
    problems = verify_run(d)
    assert problems == [f"ledger seq 2 ({rows[1]['tool_call_id']}): the arguments in the transcript "
                        f"do not match the ledger's digest"], problems


def test_the_scripts_run_as_a_reader_runs_them(tmp_path, keys):
    env = {**os.environ, "INSPEXIMUS_KEY_HOME": str(keys), "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    out = tmp_path / "out"

    def py(script, *args):
        return subprocess.run([sys.executable, str(HERE / script), *args], cwd=str(tmp_path), env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=180)

    r = py("run.py", "--out", str(out))
    assert r.returncode == 0, r.stderr[-2000:]
    assert "db-3.internal" in r.stdout and "superseded now" in r.stdout
    ok = py("verify.py", str(out))
    assert ok.returncode == 0 and ok.stdout.startswith("OK"), ok.stdout + ok.stderr[-1000:]
    ledger = out / LEDGER
    raw = bytearray(ledger.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    ledger.write_bytes(bytes(raw))
    bad = py("verify.py", str(out))
    assert bad.returncode == 1 and bad.stdout.startswith("FAIL"), bad.stdout + bad.stderr[-1000:]
