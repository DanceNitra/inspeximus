"""An erased record does not live on inside a session digest (audit A-04).

`close_session` stored the rendered block and a second copy of every entry's text in the digest
record. `forget` and `forget_subject` erased the source and never touched the digest, so the erased
words stayed in an active record that `recall` returned, while `forget`'s docstring said no merged
blob could hold them. The existing check read `session_context`, which re-resolves ids, so it could
not see the record itself.

From 3.15.2 a digest stores ids, kinds and salience; every reader renders the entries from the live
store. A digest written earlier still holds text, and its write receipt commits to that text, so it is
erased together with the record it copies.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus

MARK = "12 Elm Street"


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    path = str(tmp_path / "s.json")
    m = Inspeximus(path, receipts=True)
    m.open_session("s1")
    m.remember_decision(f"ship to Alice Novak, {MARK}", because="customer asked",
                        topic="alice-shipping", source={"doc": "crm/alice"})
    rep = m.close_session("s1")
    if not (rep.get("written") and MARK in rep.get("text", "")):
        pytest.fail("control: the session digest did not cover the record, so nothing can leak")
    return m, tmp_path, path


def _carriers(m):
    return [r.get("key") for r in m.items if MARK in json.dumps(r, ensure_ascii=False)]


def _digest(m):
    return next(r for r in m.items if r.get("key") == Inspeximus.SESSION_DIGEST_KEY and r["status"] == "active")


# ── the reproducers ──────────────────────────────────────────────────────────────────────────────────
def test_forget_subject_leaves_no_copy_in_the_session_digest(store):
    m, _, _ = store
    out = m.forget_subject("crm/alice")
    if not out.get("ids"):
        pytest.fail("control: forget_subject erased nothing")
    assert _carriers(m) == [], "an active record still carries the erased text after forget_subject"


def test_forget_by_id_leaves_no_copy_in_the_session_digest(store):
    m, _, _ = store
    target = [r["id"] for r in m.items if "decision" in (r.get("tags") or []) and MARK in r["text"]]
    if len(target) != 1:
        pytest.fail("control: expected exactly one source record to erase")
    m.forget(ids=target)
    assert _carriers(m) == [], "an active record still carries the erased text after forget"


def test_after_erasure_recall_and_disk_no_longer_hold_the_text(store):
    m, d, path = store
    m.forget_subject("crm/alice")
    m.flush()
    m2 = Inspeximus(path, receipts=True)
    assert not any(MARK in h["text"] for h in m2.recall("where does Alice live", k=10)), \
        "recall returned the erased text from the session digest"
    raw = b"".join(open(os.path.join(d, f), "rb").read() for f in os.listdir(d)
                   if os.path.isfile(os.path.join(d, f)))
    assert MARK.encode() not in raw, "the erased text is still on disk in the store or a sidecar"
    ok, problems = m2.verify_writes()
    assert ok, problems


# ── what a digest stores, and what a reader sees ─────────────────────────────────────────────────────
def test_a_digest_stores_ids_and_no_entry_text(store):
    m, _, _ = store
    d = _digest(m)
    assert MARK not in json.dumps(d, ensure_ascii=False), "the stored digest copies an entry's text"
    entries = (d.get("meta") or {}).get("entries") or []
    assert entries and all(set(e) <= {"kind", "id", "salience"} for e in entries), entries


def test_recall_renders_the_digest_from_the_live_store(store):
    m, _, _ = store
    hit = m.recall("what changed last session", k=1, reinforce=False)[0]
    if hit["id"] != _digest(m)["id"]:
        pytest.fail("control: the resume query did not reach the digest")
    assert MARK in hit["text"], "a reader of the digest no longer sees its entries"
    m.remember_decision("ship to Alice Novak, 34 Oak Avenue", because="she moved", topic="alice-shipping",
                        source={"doc": "crm/alice"})
    hit = m.recall("what changed last session", k=1, reinforce=False)[0]
    assert "34 Oak Avenue" in hit["text"] and MARK not in hit["text"], \
        "the digest shows a value that was corrected after it was written"


def test_the_next_session_number_never_repeats_after_a_digest_is_erased(store):
    m, _, _ = store
    for s in ("s2", "s3"):
        m.open_session(s)
        m.remember_decision(f"decision in {s}", because="x", topic=f"t-{s}")
        m.close_session(s)
    seqs = sorted((r.get("meta") or {}).get("session_seq") for r in m._session_digests())
    if seqs != [1, 2, 3]:
        pytest.fail(f"control: expected three digests, got {seqs}")
    m.forget(ids=[r["id"] for r in m._session_digests() if (r.get("meta") or {}).get("session_seq") == 2])
    m.open_session("s4")
    m.remember_decision("decision in s4", because="x", topic="t-s4")
    assert m.close_session("s4")["session_seq"] == 4


# ── a digest written before 3.15.2 ───────────────────────────────────────────────────────────────────
def _legacy_digest(m, entries, seq=9):
    """What close_session stored before 3.15.2: rendered text plus the entries' own text."""
    text = "SESSION DIGEST %d -- what changed in the last session (deterministic ledger diff, no LLM):\n" % seq
    text += "\n".join("  * " + e["text"] + (("  (was: %s)" % e["was"]) if e.get("was") else "") for e in entries)
    return m._stamp(text, key=Inspeximus.SESSION_DIGEST_KEY,
                    object=hashlib.sha256(text.encode()).hexdigest()[:16],
                    tags=["session-digest"], mtype="semantic", value=3.0,
                    meta={"kind": "session_digest", "session_seq": seq, "sid": "old",
                          "entries": entries, "considered": len(entries)})


def test_a_pre_3_15_1_digest_is_erased_with_the_record_it_copies(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    m.remember_decision(f"ship to Alice Novak, {MARK}", because="customer asked", topic="alice-shipping",
                        source={"doc": "crm/alice"})
    m.remember_decision("use Postgres for the ledger", because="joins", topic="db")
    alice = next(r for r in m.items if MARK in r["text"])
    other = next(r for r in m.items if "Postgres" in r["text"])
    did = _legacy_digest(m, [{"kind": "decision", "id": alice["id"], "text": alice["text"], "salience": 4.4},
                             {"kind": "decision", "id": other["id"], "text": other["text"], "salience": 4.4}])
    m.flush()
    if len(_carriers(m)) != 2:
        pytest.fail("control: the legacy digest does not hold the text beside its source")
    out = m.forget(ids=[alice["id"]], request_id="DSAR-1")
    assert did in out["derived_copies"] and did in out["ids"], out
    assert _carriers(m) == []
    assert any("Postgres" in r["text"] for r in m.items), "the erasure took a record it did not copy"
    ok, problems = m.verify_writes()
    assert ok, problems


def test_a_pre_3_15_1_digest_that_shows_an_erased_value_as_was_goes_too(tmp_path):
    """A correction entry shows the value it retired. Erasing the RETIRED record must reach the digest
    entry of the record that corrected it."""
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    m.remember(f"Alice lives at {MARK}", key="alice-address", object=MARK)
    m.remember("Alice lives at 34 Oak Avenue", key="alice-address", object="34 Oak Avenue")
    old = next(r for r in m.items if r.get("object") == MARK)
    new = next(r for r in m.items if r.get("object") == "34 Oak Avenue")
    if (old.get("meta") or {}).get("superseded_by_toggle") != new["id"]:
        pytest.fail("control: the old value is not marked as retired by the new one")
    did = _legacy_digest(m, [{"kind": "correction", "id": new["id"], "text": new["text"], "was": MARK,
                              "salience": 3.2}])
    out = m.forget(ids=[old["id"]])
    assert did in out["derived_copies"], out
    assert _carriers(m) == []


def test_a_dry_run_names_the_copies_it_would_erase(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"))
    m.remember_decision(f"ship to Alice Novak, {MARK}", because="customer asked", topic="alice-shipping")
    alice = next(r for r in m.items if MARK in r["text"])
    did = _legacy_digest(m, [{"kind": "decision", "id": alice["id"], "text": alice["text"], "salience": 4.4}])
    out = m.forget(ids=[alice["id"]], dry_run=True)
    assert out["derived_copies"] == [did] and out["would_forget"] == 2, out
    assert _carriers(m), "a dry run erased something"


def test_a_digest_from_this_version_is_never_erased_as_a_copy(store):
    m, _, _ = store
    d = _digest(m)
    alice = next(r for r in m.items if MARK in r["text"] and r.get("key") != Inspeximus.SESSION_DIGEST_KEY)
    out = m.forget(ids=[alice["id"]])
    assert out["derived_copies"] == [] and any(r["id"] == d["id"] for r in m.items), out
