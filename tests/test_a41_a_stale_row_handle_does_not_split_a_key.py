"""A-41: a stale row-store handle must not split a key, and a revert afterwards goes back one step.

Measured 2026-09-28 (AUDIT-B): handles A and B open one row store holding colour=v1. A writes v2; B, loaded
before that, writes v3. When A's commit leaves the stat signature unchanged (same mtime tick, and a row store
grows in whole pages so the size stays), B's save guard saw no change and skipped the merge: the key ended
with TWO active records, and recall served the superseded v2 beside v3. Where the signature moved, the merge
ran, but B's stale edit of v1 (superseded by v3) overwrote A's (superseded by v2), and the demoted v2 carried
no link to v3, so revert("colour") landed on v1, the value from before BOTH writes.

The same-tick arm pins the signature with os.utime, so it is deterministic on every OS. Both arms must end
with the chain v1 -> v2 -> v3, one active record, recall serving v3 only, and revert landing on v2.
"""
import os

import pytest

from inspeximus import Inspeximus


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_KEY_HOME"]:
        monkeypatch.delenv(k)


def _race(tmp_path, pin_signature: bool):
    p = str(tmp_path / "store.json")
    m = Inspeximus(p)
    for i in range(50):                                   # the file then grows by whole pages only
        m.remember(f"filler record {i}", key=f"f{i}")
    m.remember("the colour is v1", key="colour", object="v1")
    m.flush()
    a, b = Inspeximus(p), Inspeximus(p)
    st = os.stat(p)
    a.remember("the colour is v2", key="colour", object="v2")
    a.flush()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + (0 if pin_signature else 5_000_000_000)))
    if pin_signature:
        assert b._stat_sig() == b._file_sig, "control: the stat signature did not move"
    b.remember("the colour is v3", key="colour", object="v3")
    b.flush()
    return p


def _chain(p):
    rows = [r for r in Inspeximus(p).items if r.get("key") == "colour"]
    obj = {r["id"]: r.get("object") for r in rows}
    return {r.get("object"): (r.get("status"), obj.get((r.get("meta") or {}).get("superseded_by_toggle")))
            for r in rows}


@pytest.mark.parametrize("pin", [True, False], ids=["same_tick", "signature_moved"])
def test_a_stale_row_handle_leaves_one_current_value(tmp_path, pin):
    p = _race(tmp_path, pin)
    chain = _chain(p)
    assert [o for o, (s, _) in chain.items() if s == "active"] == ["v3"], chain
    hits = [h.get("text") for h in Inspeximus(p).recall("the colour is", k=5) if "colour" in (h.get("text") or "")]
    assert hits == ["the colour is v3"], hits


@pytest.mark.parametrize("pin", [True, False], ids=["same_tick", "signature_moved"])
def test_the_supersession_chain_is_the_order_of_the_writes(tmp_path, pin):
    p = _race(tmp_path, pin)
    assert _chain(p) == {"v1": ("superseded", "v2"), "v2": ("superseded", "v3"), "v3": ("active", None)}


@pytest.mark.parametrize("pin", [True, False], ids=["same_tick", "signature_moved"])
def test_a_revert_after_the_race_goes_back_one_step(tmp_path, pin):
    p = _race(tmp_path, pin)
    out = Inspeximus(p).revert("colour")
    assert out["ok"] and out["reverted_to_object"] == "v2", out
