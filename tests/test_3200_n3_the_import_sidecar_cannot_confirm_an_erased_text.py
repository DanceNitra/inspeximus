"""3.20, AUDIT-A N-3 (Low, the F8 class): the mem0 import sidecar cannot confirm an erased record by trying candidates.

THE DEFECT. `migrate.import_mem0` keeps a sidecar beside the store with a hash of every identity it imported, so a
re-import after an erasure does not write the person back. The hash was a plain sha256, and for an export with no
ids the identity is itself sha256([user_id, created_at, text]). After `forget_subject`, anyone holding the sidecar
could confirm a guessed (user, timestamp, text) by hashing it (AUDIT-A, dr/ss5/f8_class.py, on dcce3660). The module
docstring said the sidecar held "hashes of opaque ids and no text".

WHAT IS PINNED HERE. A new sidecar entry is an HMAC under the store's key-home key, so the guessed record's hash is not
in it. The sidecar still stops a re-import after the erasure, and an entry an older version wrote is still honoured.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus, migrate  # noqa: E402

ITEM = {"memory": "Alice's phone number is +421 900 123 456", "user_id": "alice", "created_at": "2026-01-02T03:04:05Z"}


def _guess(item):
    """What a holder of the sidecar computes for a guessed record, the way versions before 3.20 hashed it."""
    ident = "sha256:" + hashlib.sha256(
        json.dumps([item["user_id"], item["created_at"], item["memory"]]).encode("utf-8")).hexdigest()
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


def _sidecar(store):
    with open(migrate.sidecar_path(store), encoding="utf-8") as fh:
        return set(json.load(fh)["imported"])


def _imported_and_erased(tmp_path):
    store = Inspeximus(str(tmp_path / "m.db"))
    out = migrate.import_mem0(store, [dict(ITEM)])
    assert len(out["written"]) == 1, "CONTROL: the id-less item is imported"
    store.forget_subject("mem0/user/alice")
    assert not [r for r in store.items if "phone" in str(r.get("text"))], "CONTROL: the record is erased"
    return store


def test_a_guessed_record_is_not_confirmed_by_the_sidecar(tmp_path):
    store = _imported_and_erased(tmp_path)
    side = _sidecar(store)
    assert len(side) == 1
    assert _guess(ITEM) not in side, "the sidecar confirms a guessed (user, timestamp, text) after its erasure"
    assert _guess(dict(ITEM, memory="something else")) not in side


def test_the_sidecar_still_stops_a_reimport_after_the_erasure(tmp_path):
    store = _imported_and_erased(tmp_path)
    out = migrate.import_mem0(store, [dict(ITEM)])
    assert out["written"] == [] and out["skipped"][0]["why"] == "imported earlier and since erased", out


def test_an_entry_an_older_version_wrote_is_still_honoured(tmp_path):
    store = Inspeximus(str(tmp_path / "m.db"))
    with open(migrate.sidecar_path(store), "w", encoding="utf-8") as fh:
        json.dump({"format": "inspeximus-mem0-import/1", "imported": [_guess(ITEM)]}, fh)
    out = migrate.import_mem0(store, [dict(ITEM)])
    assert out["written"] == [] and out["skipped"][0]["why"] == "imported earlier and since erased", out
