"""AUDIT-A review of perf-317-receipts-tail (4884b049): F-18, F-19 and F-20, each failing on that head, taken in-tree by AUDIT-B.
Below the three, tests for what the fixes added: the downgrade marker, `to-tail`, and the documentation sentences.

F-18 `receipts to-legacy` on a live store is undone by the next write of any long-lived handle: the handle's cached
     mode is "tail", and after the reconcile reads an array it still takes the conversion branch, so the store is an
     object plus a tail again, and the downgrade the operator prepared does not hold.
F-19 `receipts compact` on a store that is still the legacy array converts it to the tail format, without the
     switch, although its help text says it is for a store already in that format. A pinned older server then fails
     on its next receipted write (measured: KeyError).
F-20 `verify_writes` names a record that a pinned older server saved without a receipt as "inserted out of band, or
     written while receipts were off". On a tail store the likely cause is an older version, and the text should
     say so and name the remedy (restart the older server, then recommit)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import receipts_tail as rt  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    os.makedirs(tmp_path / "st")
    return str(tmp_path / "st" / "memory.json")


def _is_array(path):
    with open(path + ".receipts.json", "rb") as fh:
        return fh.read(1) == b"["


def test_f18_to_legacy_does_not_hold_while_a_server_with_the_switch_on_is_alive(env, monkeypatch):
    """The server that converted the store still has INSPEXIMUS_RECEIPTS_TAIL=1 in its environment, which is how it
    converted the store. The operator runs `receipts to-legacy` from a shell, and the server's next write converts the
    store back (measured with two processes: array 4,008 bytes after to-legacy, object plus tail after the write)."""
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    live = Inspeximus(env, receipts=True)
    live.remember("first", key="k1")
    rt.to_legacy(env)
    assert _is_array(env) and not os.path.exists(env + ".receipts.tail.jsonl"), "control: to-legacy wrote the array"
    live.remember("second, by the handle that kept the switch on", key="k2")
    assert _is_array(env), "the open handle converted the store back; to-legacy did not hold"


def test_f18b_the_documentation_says_to_stop_servers_before_to_legacy():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "docs", "API.md"), encoding="utf-8").read()
    i = text.index("to-legacy")
    assert "stop" in text[i - 600:i + 600].lower(), "docs/API.md does not say to stop servers before `receipts to-legacy`"


def test_f19_compact_does_not_convert_a_legacy_store(env):
    m = Inspeximus(env, receipts=True)
    m.remember("first", key="k1")
    assert _is_array(env), "control: without the switch the store is the legacy array"
    try:
        rt.compact(env)
    except Exception:
        pass                                         # refusing is one allowed answer
    assert _is_array(env) and not os.path.exists(env + ".receipts.tail.jsonl"), \
        "`receipts compact` converted a legacy store to the tail format"


def test_f20_a_record_without_a_receipt_on_a_tail_store_names_the_likely_cause(env, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    Inspeximus(env, receipts=True).remember("first", key="k1")
    monkeypatch.delenv("INSPEXIMUS_RECEIPTS_TAIL")
    Inspeximus(env).remember("saved by a writer that could not extend the receipts", key="k2")   # no receipts: stands for the pinned server
    ok, problems = Inspeximus(env, receipts=True).verify_writes()
    text = " ".join(problems)
    assert not ok and "NO write receipt" in text, "control: the record is reported"
    assert "older version" in text and "restart" in text.lower(), problems


def test_f18_the_marker_to_legacy_leaves_is_a_sidecar_and_to_tail_removes_it(env, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    m = Inspeximus(env, receipts=True)
    m.remember("first", key="k1")
    rt.to_legacy(env)
    assert os.path.exists(env + ".receipts.legacy"), "to-legacy leaves a marker"
    assert not m._store_siblings()["unknown"], "the marker is a sidecar, not a copy of the records"
    out = rt.to_tail(env)
    assert out["mode_before"] == "list" and not os.path.exists(env + ".receipts.legacy")
    assert not _is_array(env) and os.path.exists(env + ".receipts.tail.jsonl")
    m.remember("after to-tail", key="k2")
    assert Inspeximus(env, receipts=True).verify_writes()[0]


def test_f19_compact_refuses_on_an_array_and_still_compacts_a_tail_store(env, monkeypatch):
    m = Inspeximus(env, receipts=True)
    m.remember("first", key="k1")
    with pytest.raises(ValueError, match="to-tail"):
        rt.compact(env)
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    m2 = Inspeximus(env, receipts=True)
    m2.remember("second", key="k2")
    assert rt.compact(env)["entries"] == 2


def test_f18_the_documentation_names_the_retry_and_the_remedy():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "docs", "API.md"), encoding="utf-8").read()
    i = text.index("Receipt tail (prototype")
    section = text[i:i + 4000].lower()
    assert "retries" in section and "twice" in section, "an agent's retry after the failed call is not documented"
    assert "recommit" in section and "to-tail" in section and "legacy" in section
