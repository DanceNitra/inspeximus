"""A-42, A-43 and their class: an operation that decides from the rows decides on the rows as they are on disk.

Measured 2026-09-28 (AUDIT-B), reproduced on v3.15.3 with the stat signature pinned and moving alike: two
handles open one store; A commits; B, loaded before that, acts. B decided from the rows it had loaded:
  - forget_subject reported success and left A's newer record of the subject on disk (A-42);
  - two credits of one record raised `good` by one, not two (A-43);
  - retire left A's new value of the key active; revert restored the value before B's view of the key,
    over A's newer one; a restatement of a value A had just retired was accepted instead of echo-blocked.
Every such operation now takes the store lock, merges what other writers committed, and only then decides
(`_decides`). Each case runs on a row store and a JSON store, with A's commit leaving the stat signature
pinned (os.utime) and moving.
"""
import os

import pytest

from inspeximus import Inspeximus


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_KEY_HOME"]:
        monkeypatch.delenv(k)


@pytest.fixture(params=["rows", "json"])
def fmt(request, monkeypatch):
    if request.param == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    return request.param


PIN = pytest.mark.parametrize("pin", [True, False], ids=["same_tick", "signature_moved"])


def _store(tmp_path, seed):
    p = str(tmp_path / "store.json")
    m = Inspeximus(p)
    for i in range(50):
        m.remember(f"filler record {i}", key=f"f{i}")
    out = seed(m)
    m.flush()
    return p, out


def _race(p, peer, stale, pin):
    a, b = Inspeximus(p), Inspeximus(p)
    st = os.stat(p)
    peer(a)
    a.flush()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + (0 if pin else 5_000_000_000)))
    out = stale(b)
    b.flush()
    return Inspeximus(p), out


def _active(f, key):
    return [r.get("object") for r in f.items if r.get("key") == key and r.get("status") == "active"]


@PIN
def test_an_erasure_reaches_a_record_a_peer_just_wrote(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("alice likes tea", source={"doc": "crm/alice"}))
    f, out = _race(p, lambda a: a.remember("alice lives in Brno", source={"doc": "crm/alice"}),
                   lambda b: b.forget_subject("crm/alice", request_id="r"), pin)
    left = [r for r in f.items if (r.get("source") or {}).get("doc") == "crm/alice"]
    assert left == [] and out["erased"] == 2, (out.get("erased"), [r.get("text") for r in left])


@PIN
def test_an_erasure_by_id_list_and_by_type_sees_the_peers_rows(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("mail bob@example.org", pii=["email"]))
    f, out = _race(p, lambda a: a.remember("mail carol@example.org", pii=["email"]),
                   lambda b: b.forget_pii(types=["email"], request_id="r"), pin)
    assert [r for r in f.items if "email" in (r.get("pii") or [])] == []


@PIN
def test_two_credits_of_one_record_both_count(tmp_path, fmt, pin):
    p, ids = _store(tmp_path, lambda m: {"r": m.remember("a useful fact", key="fact", object="x")})
    rid = ids["r"]
    f, _ = _race(p, lambda a: a.credit(rid, 1.0), lambda b: b.credit(rid, 1.0), pin)
    assert next(r for r in f.items if r["id"] == rid).get("good") == 2.0


@PIN
def test_retire_ends_the_value_a_peer_just_wrote(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("colour v1", key="colour", object="v1"))
    f, _ = _race(p, lambda a: a.remember("colour v2", key="colour", object="v2"),
                 lambda b: b.retire("colour", reason="no longer tracked"), pin)
    assert _active(f, "colour") == []


@PIN
def test_revert_steps_back_from_the_current_value_not_from_a_stale_one(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: (m.remember("colour v1", key="colour", object="v1"),
                                       m.remember("colour v2", key="colour", object="v2")))
    f, out = _race(p, lambda a: a.remember("colour v3", key="colour", object="v3"),
                   lambda b: b.revert("colour"), pin)
    assert out["reverted_to_object"] == "v2" and _active(f, "colour") == ["v2"], (out, _active(f, "colour"))


@PIN
def test_a_restatement_of_a_value_a_peer_just_retired_is_echo_blocked(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("colour v1", key="colour", object="v1"))
    f, _ = _race(p, lambda a: a.remember("colour v2", key="colour", object="v2"),
                 lambda b: b.remember("colour v1", key="colour", object="v1"), pin)
    assert _active(f, "colour") == ["v2"]


def test_the_lock_is_held_once_and_released(tmp_path):
    """Re-entrancy: a deciding operation that calls another keeps the one hold, and the lock is free after,
    so a second handle can decide at once (a stuck lock would block it)."""
    p, ids = _store(tmp_path, lambda m: {"r": m.remember("a useful fact", key="fact", object="x")})
    m = Inspeximus(p)
    import threading
    me = threading.get_ident()
    with m._deciding():
        assert m._decide_depths[me] == 1
        m.credit(ids["r"], 1.0)
        assert m._decide_depths[me] == 1
    assert me not in m._decide_depths
    Inspeximus(p).credit(ids["r"], 1.0)


@PIN
def test_an_erasure_that_misses_a_record_says_so(tmp_path, fmt, pin, monkeypatch):
    """The second half of A-42: the report is re-checked against the file after the save. With the
    decision's sync switched off (the 3.15.3 behaviour), the peer's record survives; the result must then
    fail its residue check and name the record, instead of reporting a clean erasure."""
    p, _ = _store(tmp_path, lambda m: m.remember("alice likes tea", source={"doc": "crm/alice"}))
    monkeypatch.setattr(Inspeximus, "_sync_before_decision", lambda self: False)
    if fmt == "json":
        # A JSON save cannot merge, so the stale erasure is REFUSED (A-37's guard) rather than completed
        # with a survivor: the loud outcome, which needs no after-the-fact check.
        from inspeximus.core import StoreChangedOnDisk
        with pytest.raises(StoreChangedOnDisk):
            _race(p, lambda a: a.remember("alice lives in Brno", source={"doc": "crm/alice"}),
                  lambda b: b.forget_subject("crm/alice", request_id="r"), pin)
        return
    f, out = _race(p, lambda a: a.remember("alice lives in Brno", source={"doc": "crm/alice"}),
                   lambda b: b.forget_subject("crm/alice", request_id="r"), pin)
    left = [r["id"] for r in f.items if (r.get("source") or {}).get("doc") == "crm/alice"]
    assert left, "control: without the sync the peer's record survives"
    assert out["subject_left_on_disk"] == left
    assert out["residue_in_store"]["ok"] is False
    assert any(x.get("record") == left[0] for x in out["residue_in_store"]["findings"])


@PIN
def test_two_stale_spends_cannot_overdraw_the_budget(tmp_path, fmt, pin):
    p, ids = _store(tmp_path, lambda m: {"r": m.remember("an action target")})
    a, b = Inspeximus(p), Inspeximus(p)
    ra = a.spend_irreversible([ids["r"]], amount=0.6, budget=1.0)
    a.flush()
    rb = b.spend_irreversible([ids["r"]], amount=0.6, budget=1.0)
    b.flush()
    assert ra["allowed"] is True and rb["allowed"] is False, (ra, rb)


@PIN
def test_an_objection_a_peer_recorded_is_kept(tmp_path, fmt, pin):
    def seed(m):
        m.remember("alice likes tea", source={"doc": "crm/alice"})
        m.remember("bob likes tea", source={"doc": "crm/bob"})
    p, _ = _store(tmp_path, seed)
    f, _ = _race(p, lambda a: a.object_processing("crm/alice", "dpo", "own_situation", allow_ambiguous=True),
                 lambda b: b.object_processing("crm/bob", "dpo", "own_situation", allow_ambiguous=True), pin)
    assert sorted(o["subject"] for o in f._objections) == ["crm/alice", "crm/bob"]


@PIN
def test_forget_by_predicate_reaches_a_record_a_peer_just_wrote(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("temp note one", tags=["scratch"]))
    f, _ = _race(p, lambda a: a.remember("temp note two", tags=["scratch"]),
                 lambda b: b.forget(where=lambda r: "scratch" in (r.get("tags") or [])), pin)
    assert [r for r in f.items if "scratch" in (r.get("tags") or [])] == []


@PIN
def test_a_revert_intent_is_judged_against_the_current_value(tmp_path, fmt, pin):
    """submit_revert with a relative intent minted by the stale handle: the base it names was moved by the
    peer, so the revert must report a conflict rather than land on a value nobody holds current."""
    p, _ = _store(tmp_path, lambda m: (m.remember("colour v1", key="colour", object="v1"),
                                       m.remember("colour v2", key="colour", object="v2")))
    a, b = Inspeximus(p), Inspeximus(p)
    intent = b.revert_intent("colour")
    st = os.stat(p)
    a.remember("colour v3", key="colour", object="v3")
    a.flush()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + (0 if pin else 5_000_000_000)))
    out = b.submit_revert(intent)
    b.flush()
    assert out["ok"] is False and out["reason"] == "conflict", out
    assert _active(Inspeximus(p), "colour") == ["v3"]


@PIN
def test_an_objection_a_peer_recorded_can_be_resolved_by_another_handle(tmp_path, fmt, pin):
    p, _ = _store(tmp_path, lambda m: m.remember("alice likes tea", source={"doc": "crm/alice"}))
    f, out = _race(p, lambda a: a.object_processing("crm/alice", "dpo", "own_situation"),
                   lambda b: b.resolve_objection("crm/alice", "dpo", "upheld"), pin)
    assert [o["status"] for o in f._objections] == ["upheld"], f._objections


def test_an_erasure_on_an_encrypted_store_checks_the_store_again_with_the_callers_key(tmp_path):
    """The post-save check reads the store with a fresh handle. Opened from the path alone it could not
    decrypt, so forget_subject raised on every encrypted store (probes/erasure_edgecases_probe.py). The
    check must read with the caller's key and still find a record of the subject left on disk."""
    pytest.importorskip("cryptography")
    from inspeximus import new_encryption_key
    key = new_encryption_key()
    p = str(tmp_path / "enc.json")
    a = Inspeximus(p, encrypt_key=key)
    a.remember("alice lives in Rome", source={"doc": "alice"})
    a.flush()
    out = a.forget_subject("alice", request_id="enc-1")
    assert out["erased"] == 1, out
    assert out["subject_left_on_disk"] == []
    assert out["residue_in_store"]["ok"] is True, out["residue_in_store"]
    assert not any("alice" in r.get("text", "") for r in Inspeximus(p, encrypt_key=key).items)
