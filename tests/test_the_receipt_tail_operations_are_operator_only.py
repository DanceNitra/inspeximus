"""The receipt-tail operations rewrite the store-wide receipt sidecar, so a tenant- or agent-bound handle cannot run them (3.17,
the class of AUDIT-A F-44).

`receipts_compact`, `receipts_to_legacy` and `receipts_to_tail` are the library form of `inspeximus receipts compact`, `to-legacy` and
`to-tail`. Each refuses on a tenant view and on an agent view, with a message that says why, and changes nothing; the unbound handle
is the control and does the work.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus import receipts_tail  # noqa: E402

OPS = ("receipts_compact", "receipts_to_legacy", "receipts_to_tail")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "kh"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    return tmp_path


def _store(env, n=5):
    p = str(env / "s.json")
    m = Inspeximus(path=p, receipts=True)
    for i in range(n):
        m.remember("a fact number %d about the release" % i, key="k%d" % i)
    m.flush()
    return p, m


def _sidecar_bytes(p):
    out = {}
    for suffix in (".receipts.json", ".receipts.tail.jsonl", ".receipts.legacy"):
        if os.path.exists(p + suffix):
            out[suffix] = open(p + suffix, "rb").read()
    return out


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("bind", ["tenant", "agent"])
def test_a_bound_handle_cannot_run_a_receipt_tail_operation(env, op, bind):
    p, m = _store(env)
    before = _sidecar_bytes(p)
    assert "inspeximus.receipts/2" in before[".receipts.json"].decode("utf-8"), "fixture: the store is in the tail format"
    view = m.for_tenant("tenant-a") if bind == "tenant" else m.as_agent("agent-a")
    with pytest.raises(AttributeError, match="operator-only"):
        getattr(view, op)()
    assert _sidecar_bytes(p) == before, "a refused operation must change nothing"


def test_control_the_unbound_handle_runs_them_in_order(env):
    p, m = _store(env)
    out = m.receipts_compact()
    assert out["entries"] == 5 and out["snapshot_after"] == 5, out
    out = m.receipts_to_legacy()
    assert out["mode_before"] == "tail" and out["entries"] == 5, out
    assert json.loads(open(p + ".receipts.json", encoding="utf-8").read())[0].get("hash"), "an array again"
    assert not os.path.exists(p + ".receipts.tail.jsonl")
    out = m.receipts_to_tail()
    assert out["mode_before"] != "tail" and out["entries"] == 5, out
    assert "inspeximus.receipts/2" in open(p + ".receipts.json", encoding="utf-8").read()


def test_the_cli_commands_still_work_on_their_own_unbound_handle(env):
    import subprocess
    p, _ = _store(env)
    e = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cmd in ("compact", "to-legacy", "to-tail"):
        r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p, "receipts", cmd], capture_output=True, text=True,
                           encoding="utf-8", env=e, timeout=120)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr[-300:])
