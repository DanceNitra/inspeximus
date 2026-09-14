"""`_load_from_disk` reads the file, then stamps the signature. A write in between is invisible.

THE DEFECT, in order. `_load_from_disk` calls `self.path.read_bytes()` and only afterwards sets
`self._file_sig = self._stat_sig()`. Nothing holds the store lock across those two lines: the lock
covers `_save`, not the load. So a writer that replaces the file in between leaves this handle
holding the OLD records under the NEW signature. The next `_save` compares the two, sees no change,
and rewrites the whole file from the stale view. The JSON path cannot merge, so the other writer's
record is gone and that writer was told `remember()` succeeded.

WHY THIS IS WORTH A DETERMINISTIC TEST. The same loss shows up in
`probes/what_a_concurrent_writer_is_told_against_what_the_store_keeps.py` at roughly one record in
900, with the lock held on every write, which ruled out the degraded-lock path. The next suspect was
the signature's own fields: `(mtime_ns, size)` collides, measured at 211 of 1,500 same-length
writes in the receipt at 2e8483a (this docstring said 119 until 2026-09-14; the number had been
typed, not read). `probes/does_a_wider_change_signature_stop_the_silent_loss.py` tested that by
adding `st_ino`, which changes on every atomic write: the widened arm lost nothing, and so did the
arm that only fixed the ordering, while the arm carrying the old ordering lost 9 of 2,880. A guard
that cannot collide gains nothing once the ordering is right, so the fields were never the
mechanism. The ordering is, and unlike the race it can be reproduced exactly.

The tests below inject the competing write inside the read, so there is no timing to get lucky with.
"""
from __future__ import annotations

import contextlib
import json
import pathlib

import pytest

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk


def _texts(path) -> set[str]:
    with open(path, encoding="utf-8") as fh:
        return {r.get("text") for r in json.load(fh)}


@pytest.fixture()
def json_store(tmp_path, monkeypatch):
    """A plain JSON store. The row store merges by id, so this defect does not reach it."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / "memory.json"
    seed = Inspeximus(path=str(path))
    seed._save_min_s = 0
    seed.remember("seed record", key="seed", mtype="fact")
    seed.flush()
    return path


def _load_with_a_write_in_the_middle(path, injected_text: str):
    """Open a handle whose load is interrupted by another writer, then let it save.

    The interruption is the real sequence, not a simulation of it: a genuine second handle performs
    a genuine atomic write, and it happens after this handle's bytes have been read and before its
    signature is taken.
    """
    real_read = pathlib.Path.read_bytes
    fired = {"n": 0}

    def read_then_let_the_other_writer_in(self, *a, **k):
        raw = real_read(self, *a, **k)
        if str(self) == str(path) and fired["n"] == 0:
            fired["n"] = 1
            other = Inspeximus(path=str(path))
            other._save_min_s = 0
            other.remember(injected_text, key="other", mtype="fact")
            other.flush()
        return raw

    pathlib.Path.read_bytes = read_then_let_the_other_writer_in
    try:
        late = Inspeximus(path=str(path))
        late._save_min_s = 0
    finally:
        pathlib.Path.read_bytes = real_read
    assert fired["n"] == 1, "the injected write never ran, so this test measured nothing"
    return late


def test_a_write_that_lands_between_the_read_and_the_signature_is_not_overwritten(json_store):
    late = _load_with_a_write_in_the_middle(json_store, "written by the other writer")
    # A REFUSAL IS A PASS HERE, and it is what the fix produces. The failure this test exists to
    # catch is the opposite one: the call returning normally with the other writer's record gone.
    # Asserting that no exception is raised would have made the remedy look like a regression.
    # The suppression covers `remember` as well: with `_save_min_s` at 0 the write is persisted on
    # the spot, so the refusal surfaces there and never reaches `flush`.
    with contextlib.suppress(StoreChangedOnDisk):
        late.remember("written by the late handle", key="late", mtype="fact")
        late.flush()

    on_disk = _texts(json_store)
    assert "written by the other writer" in on_disk, (
        "the other writer was told its record was stored and it is not in the file. The late "
        "handle read the file before that write and stamped its signature after it, so `_save` "
        "compared a signature that already described the other writer's file, found no change, "
        "and rewrote the whole store from records that predate it. On disk: %s" % sorted(on_disk))


def test_the_late_handle_still_persists_its_own_record(json_store):
    """The remedy must not turn a silent loss into a silent refusal of the caller's own write."""
    late = _load_with_a_write_in_the_middle(json_store, "written by the other writer")
    try:
        late.remember("written by the late handle", key="late", mtype="fact")
        late.flush()
    except Exception:
        # A refusal is an acceptable outcome here: it is honest, and the caller can retry. What is
        # not acceptable is success with the record absent, which the next assertion checks for.
        late = Inspeximus(path=str(json_store))
        late._save_min_s = 0
        late.remember("written by the late handle", key="late", mtype="fact")
        late.flush()
    assert "written by the late handle" in _texts(json_store)


def test_the_control_a_load_with_no_competing_write_keeps_everything(json_store):
    """Without the injected write both records survive, so a failure above is the interleaving.

    This is the arm that fails if the fixture stops reproducing the situation, rather than if the
    store stops being correct.
    """
    a = Inspeximus(path=str(json_store))
    a._save_min_s = 0
    a.remember("written by the late handle", key="late", mtype="fact")
    a.flush()
    on_disk = _texts(json_store)
    assert {"seed record", "written by the late handle"} <= on_disk
