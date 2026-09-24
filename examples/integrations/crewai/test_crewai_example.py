"""The CrewAI example, end to end, against the pinned CrewAI, with no API key and no network.

    pip install -r examples/integrations/crewai/requirements.txt
    pytest examples/integrations/crewai

Three claims, each with the check that could prove it wrong:

  1. the crew's agent memory is inspeximus       -> the memories the agent acted on are inspeximus records
                                                    with signed write receipts, read back from the store
  2. every tool call is in the ledger, with what  -> the tools count their own executions and CrewAI's
     it was based on                                event bus counts its own; both must equal the ledger,
                                                    and each entry's basis must resolve in the store
  3. the verifier fails when one entry changes    -> every one-byte edit to the ledger (substitute, delete,
                                                    insert, at every position) must fail; the unedited
                                                    ledger must pass, or the sweep proves nothing
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
from importlib.metadata import version

import pytest

try:
    import crewai  # noqa: F401
except ModuleNotFoundError as e:                  # a clear error, never a silent skip
    raise ImportError("these tests need the pinned CrewAI: "
                      "pip install -r examples/integrations/crewai/requirements.txt") from e

import crew as crew_mod
import negative_control
import tool_ledger
import verify_ledger
from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, _entry_hash, _sign, verify_file

HERE = pathlib.Path(__file__).resolve().parent
PINNED = "1.15.22"
LEDGER = crew_mod.STORE_NAME + ".actions.json"

# Loaded into the crew's subprocess as sitecustomize: every outbound connection attempt is logged and
# refused. It writes a marker when it loads, so "no attempts" cannot be the result of a guard that
# never ran.
NETWORK_GUARD = r'''
import os, socket
_log = os.environ["NETWORK_GUARD_LOG"]
open(_log + ".loaded", "w").close()
def _deny(*a, **k):
    with open(_log, "a") as f:
        f.write(repr(a[1:]) + "\n")
    raise OSError("network disabled by the test")
socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.create_connection = _deny
'''


def _clean_env(extra: dict) -> dict:
    """The parent's environment minus every credential-shaped variable."""
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"(API_KEY|_TOKEN|_SECRET|PASSWORD)$", k, re.IGNORECASE)}
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    """One real run of crew.py, as a reader would start it, with the network guard loaded."""
    base = tmp_path_factory.mktemp("crewai_example")
    guard = base / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    log = base / "network.log"
    env = _clean_env({"NETWORK_GUARD_LOG": str(log), "PYTHONIOENCODING": "utf-8",
                      "PYTHONPATH": os.pathsep.join([str(guard), os.environ.get("PYTHONPATH", "")])})
    out = base / "run"
    p = subprocess.run([sys.executable, str(HERE / "crew.py"), "--out", str(out)], cwd=str(base), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    return {"dir": out, "stdout": p.stdout, "log": log, "env": env}


def _copy(run, tmp_path) -> pathlib.Path:
    dst = tmp_path / "run"
    shutil.copytree(run["dir"], dst)
    return dst


def _write_entries(path: pathlib.Path, entries: list) -> None:
    """Write a ledger the way ActionLedger._save does, so only the content differs, never the form."""
    path.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")


# ── the pin ──────────────────────────────────────────────────────────────────────────────────────

def test_the_installed_crewai_is_the_pinned_one():
    """The pin is stated in four places. They must agree with each other and with what is installed,
    or a green run here says nothing about the version the README promises."""
    reqs = (HERE / "requirements.txt").read_text(encoding="utf-8")
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^crewai==%s$" % re.escape(PINNED), reqs, re.M), "requirements.txt"
    assert "crewai==" + PINNED in readme, "README.md"
    assert tool_ledger.TESTED_CREWAI == PINNED, "tool_ledger.TESTED_CREWAI"
    assert version("crewai") == PINNED, (
        f"crewai {version('crewai')} is installed; this example is pinned to {PINNED}. The other tests "
        f"still say whether {version('crewai')} works, but not that the pin does.")


# ── claim 0: no key, no network ──────────────────────────────────────────────────────────────────

def test_the_crew_runs_with_no_api_key_and_no_network(run_dir):
    assert not [k for k in run_dir["env"] if k.upper().endswith("API_KEY")]
    assert pathlib.Path(str(run_dir["log"]) + ".loaded").exists(), "the network guard never loaded"
    attempts = run_dir["log"].read_text(encoding="utf-8") if run_dir["log"].exists() else ""
    assert attempts == "", "the crew tried to open a connection:\n" + attempts
    summary = json.loads((run_dir["dir"] / "run.json").read_text(encoding="utf-8"))
    assert summary["crewai"] == PINNED
    assert summary["stub_llm_calls"].get("agent", 0) >= 3, summary["stub_llm_calls"]


# ── claim 1: the agent's memory is inspeximus ─────────────────────────────────────────────────────

def test_the_agents_memory_is_inspeximus(run_dir):
    """What CrewAI remembered is in the inspeximus store, each record under a signed write receipt:
    the two seeded facts and the result CrewAI saved after the task."""
    pk = (run_dir["dir"] / "receipt.pub").read_text(encoding="utf-8").strip()
    store = Inspeximus(path=str(run_dir["dir"] / crew_mod.STORE_NAME), receipts=True)
    texts = [r["text"] for r in store.items if r.get("status") == "active"]
    assert any("up to 50 EUR" in t for t in texts), texts
    assert any("Order 4711 was placed" in t for t in texts), texts
    assert any("Refund issued" in t for t in texts), "CrewAI's post-task save did not reach inspeximus"
    assert all("crewai-backend" in (r.get("tags") or []) for r in store.items)
    ok, problems = store.verify_writes(expected_pubkey=pk)
    assert ok, problems


# ── claim 2: every tool call, with its basis ──────────────────────────────────────────────────────

def test_every_tool_call_is_in_the_ledger(run_dir):
    s = json.loads((run_dir["dir"] / "run.json").read_text(encoding="utf-8"))
    assert s["tool_calls_executed"] == ["lookup_order", "issue_refund"]
    assert s["tool_calls_in_ledger"] == s["tool_calls_executed"]
    assert sorted(s["tool_calls_seen_by_crewai"]) == sorted(s["tool_calls_executed"])
    assert s["every_tool_call_recorded"] is True


def test_each_entry_names_what_it_was_based_on(run_dir):
    store = Inspeximus(path=str(run_dir["dir"] / crew_mod.STORE_NAME), receipts=True)
    led = ActionLedger(store)
    look, refund = led.entries()
    by_id = {r["id"]: r for r in store.items}
    receipts = [r["hash"] for r in store._receipts]

    for e in (look, refund):
        basis = e["meta"]["based_on"]
        ids = [m["id"] for m in basis["memories"]]
        assert ids and all(i in by_id for i in ids), ids
        assert e["memory_state"]["recalled"] == ids
        # bound to the receipt chain as it stood when the tool was called
        n = e["memory_state"]["receipts"]
        assert e["memory_state"]["last_receipt"] == receipts[n - 1]
        # and what_it_knew() answers from the entry, through the store's provenance
        knew = led.what_it_knew(e["seq"])
        assert [p["found"] for p in knew["recalled_now"]] == [True] * len(ids)

    policy = [i for i in (m["id"] for m in refund["meta"]["based_on"]["memories"])
              if "up to 50 EUR" in by_id[i]["text"]]
    assert policy, "the refund entry does not name the policy memory the refund amount came from"
    assert refund["meta"]["based_on"]["earlier_tool_calls"] == [{"seq": 0, "hash": look["hash"]}]

    # The digests bind the actual arguments: the true call matches, a one-cent change does not.
    call = {"tool": "issue_refund", "args": {"order_id": "4711", "amount_eur": 40.0, "reason": "damaged item"}}
    assert led.matches(1, inputs=call)["inputs"] is True
    call["args"]["amount_eur"] = 40.01
    assert led.matches(1, inputs=call)["inputs"] is False


def test_the_tool_call_follows_the_memory(tmp_path):
    """The basis is not decoration: change the remembered limit and the refund changes with it; take
    the limit out of memory and there is no refund call at all."""
    s = crew_mod.run(tmp_path / "limit30", policy="Refund policy: support may refund up to 30 EUR without a manager.")
    assert s["tool_calls_executed"] == ["lookup_order", "issue_refund"] and s["every_tool_call_recorded"]
    led = ActionLedger(Inspeximus(path=s["store"], receipts=True))
    call = {"tool": "issue_refund", "args": {"order_id": "4711", "amount_eur": 30.0, "reason": "damaged item"}}
    assert led.matches(1, inputs=call)["inputs"] is True
    assert verify_ledger.verify(tmp_path / "limit30")["ok"]

    s = crew_mod.run(tmp_path / "nolimit", policy="Support answers every customer within one working day.")
    assert s["tool_calls_executed"] == ["lookup_order"] and s["every_tool_call_recorded"]
    assert "escalate" in s["result"]
    assert verify_ledger.verify(tmp_path / "nolimit")["ok"]


def test_a_ledger_that_cannot_be_written_stops_every_later_tool(tmp_path, monkeypatch):
    """CrewAI swallows hook exceptions except HookAborted, and turns a HookAborted from an after-hook
    into an "Error executing tool" observation. So: the first write fails after lookup_order ran; the
    agent never sees the order (it escalates instead of refunding); issue_refund never runs; and the
    run exits 1 because the ledger is one call short. No hook can stop the crew itself."""
    real = ActionLedger.record
    calls = {"n": 0}

    def failing_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated: disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(ActionLedger, "record", failing_once)
    assert crew_mod.main(["--out", str(tmp_path / "broken")]) == 1
    s = json.loads((tmp_path / "broken" / "run.json").read_text(encoding="utf-8"))
    assert s["tool_calls_executed"] == ["lookup_order"], "a tool ran after the ledger had failed"
    assert s["tool_calls_in_ledger"] == []
    assert s["every_tool_call_recorded"] is False
    assert "escalate" in s["result"], "the agent acted on a tool result the ledger never recorded"


def test_a_recorder_bound_to_one_crew_ignores_another(tmp_path):
    """The hooks are process-wide, so two crews in one process would each record the other's calls.
    `crew=` scopes a recorder to its own crew."""
    from types import SimpleNamespace
    led = ActionLedger(path=tmp_path / "l.json")
    mine, other = SimpleNamespace(id="crew-A"), SimpleNamespace(id="crew-B")
    rec = tool_ledger.CrewToolLedger(led, backend=None, crew=mine)
    ctx = SimpleNamespace(crew=other, tool_name="t", tool_input={}, task=None, agent=None, tool_result="r")
    assert rec.before(ctx) is None and rec.after(ctx) is None
    assert len(led) == 0 and rec.recorded == []


def test_the_hooks_are_gone_after_the_run(tmp_path):
    """CrewAI's tool hooks are process-wide; the recorder must not keep recording other crews."""
    from crewai.hooks import get_after_tool_call_hooks, get_before_tool_call_hooks

    def ours():
        return [h for h in get_before_tool_call_hooks() + get_after_tool_call_hooks()
                if isinstance(getattr(h, "__self__", None), tool_ledger.CrewToolLedger)]

    with tool_ledger.CrewToolLedger(ledger=None, backend=None).installed():
        assert len(ours()) == 2, "the control: inside the block both hooks must be registered"
    assert ours() == []
    crew_mod.run(tmp_path / "again")
    assert ours() == []


# ── claim 3: the verifier ─────────────────────────────────────────────────────────────────────────

def test_the_verifier_passes_the_unedited_run(run_dir):
    r = verify_ledger.verify(run_dir["dir"])
    assert r["ok"], r["problems"]
    assert r["entries"] == 2
    p = subprocess.run([sys.executable, str(HERE / "verify_ledger.py"), str(run_dir["dir"])],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "OK action ledger" in p.stdout, p.stdout + p.stderr


def test_the_verifier_does_not_need_crewai(run_dir, tmp_path):
    """An auditor verifies with inspeximus alone. CrewAI is made unimportable in the subprocess."""
    block = tmp_path / "block"
    block.mkdir()
    (block / "sitecustomize.py").write_text(
        "import sys\n"
        "class _NoCrew:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'crewai' or name.startswith('crewai.'):\n"
        "            raise ImportError('crewai is blocked in this test')\n"
        "sys.meta_path.insert(0, _NoCrew())\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(block), os.environ.get("PYTHONPATH", "")]))
    p = subprocess.run([sys.executable, "-c", "import crewai"], env=env, capture_output=True, text=True)
    assert p.returncode != 0, "the block did not hold, so this test would prove nothing"
    p = subprocess.run([sys.executable, str(HERE / "verify_ledger.py"), str(run_dir["dir"])], env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_every_one_byte_edit_to_the_ledger_fails(run_dir):
    """THE NEGATIVE CONTROL. At every byte position: the hardest substitution for that byte, a
    deletion, and an inserted space. Not one edited ledger may verify, and the unedited one must."""
    r = negative_control.sweep(run_dir["dir"])
    assert r["baseline_ok"], "the unedited ledger does not verify, so every failure below is vacuous"
    assert r["edits"] == {"substitute": r["bytes"], "delete": r["bytes"], "insert": r["bytes"]}
    assert r["passed"] == [], f"{len(r['passed'])} one-byte edits verified:\n" + "\n".join(r["passed"][:20])


def test_the_sweep_can_fail(run_dir, monkeypatch):
    """The control on the control. With the byte check switched off the verifier is the chain check
    alone, and the sweep must then report edits that got through; if it reported none, it could not
    tell a strong verifier from a weak one."""
    def parse_only(raw):
        try:
            return json.loads(raw.decode("utf-8")), []
        except (UnicodeDecodeError, ValueError) as e:
            return None, [f"bytes: {e}"]

    monkeypatch.setattr(verify_ledger, "check_bytes", parse_only)
    r = negative_control.sweep(run_dir["dir"], kinds=("substitute",))
    assert r["baseline_ok"]
    assert r["passed"], "with step 1 disabled no edit got through, so the sweep cannot see a weak verifier"


def test_the_script_exits_nonzero_on_a_one_byte_edit(run_dir, tmp_path):
    """The same control through the command a reader runs: exit 1 on the edit, 0 once it is undone."""
    d = _copy(run_dir, tmp_path)
    ledger = d / LEDGER
    raw = ledger.read_bytes()
    i = raw.index(b"tool:issue_refund") + len(b"tool:")          # "issue_refund" -> "hssue_refund"
    ledger.write_bytes(raw[:i] + bytes([raw[i] ^ 0x01]) + raw[i + 1:])
    cmd = [sys.executable, str(HERE / "verify_ledger.py"), str(d)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 1 and "FAIL" in p.stdout, p.stdout + p.stderr
    ledger.write_bytes(raw)
    assert subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode == 0


def test_the_byte_check_catches_what_the_chain_cannot_see(run_dir, tmp_path):
    """Why verify_ledger.py has a step before the chain check. Two one-byte edits that leave every hash
    and signature valid: a space turned into a tab between tokens, and one hex digit of a signature put
    in upper case (it decodes to the same signature). `verify_file` accepts both; the script must not."""
    d = _copy(run_dir, tmp_path)
    ledger = d / LEDGER
    raw = ledger.read_bytes()
    pk = (d / "receipt.pub").read_text(encoding="utf-8").strip()
    i = raw.index(b'"sig": "') + len(b'"sig": "')
    j = next(k for k in range(i, i + 128) if chr(raw[k]) in "abcdef")
    for edited in (raw.replace(b'\n  "v"', b'\n \t"v"', 1),
                   raw[:j] + raw[j:j + 1].upper() + raw[j + 1:]):
        assert edited != raw
        ledger.write_bytes(edited)
        assert verify_file(ledger, expected_pubkey=pk)[0], "the chain check alone now sees this edit"
        r = verify_ledger.verify(d)
        assert not r["ok"] and any(p.startswith("bytes:") for p in r["problems"]), r


def test_a_rewritten_memory_fails(run_dir, tmp_path):
    """The ledger is bound to the memories it names. Rewrite the policy the refund was based on,
    directly in the store's database, and verification fails on the memory and on the basis."""
    d = _copy(run_dir, tmp_path)
    con = sqlite3.connect(str(d / crew_mod.STORE_NAME))
    with con:
        n = con.execute("UPDATE records SET doc = replace(doc, 'up to 50 EUR', 'up to 90 EUR')").rowcount
    con.close()
    assert n >= 1
    r = verify_ledger.verify(d)
    assert not r["ok"]
    assert any(p.startswith("memory:") for p in r["problems"]), r["problems"]
    assert any(p.startswith("basis:") and "does not match its write receipt" in p for p in r["problems"]), r["problems"]


def test_an_erased_basis_memory_is_a_note_and_a_vanished_one_is_a_failure(run_dir, tmp_path):
    """CrewAI may later update or delete a memory an earlier call was based on, through `forget()`,
    which leaves a signed tombstone. That is history, not tampering, and the run still verifies. A row
    that disappears from the database with no tombstone is tampering."""
    sk, pk = new_receipt_keypair()                      # an operator-held key, so a later forget is signed
    s = crew_mod.run(tmp_path / "kept", receipt_key=(sk, pk))
    store = Inspeximus(path=s["store"], receipts=True, receipt_key=sk, receipt_pubkey=pk)
    refund = ActionLedger(store).entries()[1]
    policy_id = next(i for i in refund["memory_state"]["recalled"]
                     if "up to 50 EUR" in next(r["text"] for r in store.items if r["id"] == i))
    backup = tmp_path / "before_forget"
    shutil.copytree(tmp_path / "kept", backup)

    store.forget(ids=[policy_id], basis="consolidation replaced it")
    store.flush()
    r = verify_ledger.verify(tmp_path / "kept")
    assert r["ok"], r["problems"]
    assert any("erased after the call" in n for n in r["notes"]), r["notes"]

    con = sqlite3.connect(str(backup / crew_mod.STORE_NAME))
    with con:
        assert con.execute("DELETE FROM records WHERE id = ?", (policy_id,)).rowcount == 1
    con.close()
    r = verify_ledger.verify(backup)
    assert not r["ok"]
    assert any("left no tombstone" in p for p in r["problems"]), r["problems"]


def test_a_re_signed_ledger_fails_even_without_a_pinned_key(run_dir, tmp_path):
    """Change one entry, then recompute and re-sign the WHOLE chain with a new key, so every hash, link
    and signature is valid. The pinned key catches it; with no pin at all, the ledger is still not
    signed by the key that signs the store's receipts."""
    d = _copy(run_dir, tmp_path)
    entries = json.loads((d / LEDGER).read_text(encoding="utf-8"))
    entries[1]["meta"]["task"] = "something else"
    sk, _pk = new_receipt_keypair()
    prev = "0" * 64
    for e in entries:
        for f in ("hash", "sig", "pubkey"):
            e.pop(f, None)
        e["prev"] = prev
        e["hash"] = _entry_hash(e)
        e["sig"], e["pubkey"] = _sign(sk, e["hash"])
        prev = e["hash"]
    _write_entries(d / LEDGER, entries)
    assert verify_file(d / LEDGER)[0], "the forgery is not internally consistent, so it tests nothing"

    r = verify_ledger.verify(d)
    assert not r["ok"] and any("unexpected key" in p for p in r["problems"]), r["problems"]
    (d / "receipt.pub").unlink()
    (d / "run.json").unlink()
    r = verify_ledger.verify(d)
    assert not r["ok"], "a ledger re-signed with a stranger's key verified"
    assert any("not signed by the key that signs the store's receipts" in p for p in r["problems"]), r["problems"]


def test_a_cut_tail_is_caught_by_the_pin_and_only_by_the_pin(run_dir, tmp_path):
    """A hash chain cannot see its own last entry removed; that is the ledger's documented limit, and
    the reason run.json pins the count and the tail. With the pin: FAIL. Without it: the shortened
    ledger verifies, and this test says so rather than pretending otherwise."""
    d = _copy(run_dir, tmp_path)
    entries = json.loads((d / LEDGER).read_text(encoding="utf-8"))
    _write_entries(d / LEDGER, entries[:-1])
    r = verify_ledger.verify(d)
    assert not r["ok"] and any(p.startswith("pins:") for p in r["problems"]), r["problems"]
    (d / "run.json").unlink()
    assert verify_ledger.verify(d)["ok"], "if this fails the limit is gone: update README.md"
