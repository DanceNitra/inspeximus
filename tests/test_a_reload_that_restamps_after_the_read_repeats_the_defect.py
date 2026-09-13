"""`reload()` re-stamped the signature AFTER its read, which is the defect 2.27.1 fixed in
`_load_from_disk`, one call site over.

THE INSTANCE AND THE CLASS. 2.27.1 moved `_load_from_disk`'s signature stamp to before the read, and
the 30-round probe went from 9 lost to 0. `reload()` calls `_load_from_disk` and then, after merging
this handle's own records back in, assigns `self._file_sig = self._stat_sig()` again: a fresh stat,
taken after the read, outside the lock. A write that lands between the read and that stat leaves the
reloading handle holding records that predate the write under a signature that postdates it. Its
next `_save` compares equal signatures, sees no change, and rewrites the file from the stale view.

WHY IT SURVIVED THE PROBE. `reload()` runs only on the retry path, after a save was refused, so it is
entered far less often than a plain load. CI caught it on 2026-09-13 at 0a26545 on a 2-vCPU runner:
`1 of 96 records that a writer was TOLD had been written are not in the store`, lock held on every
writer, tree already stamping before the read, shape "scattered singles", record w7:r0. That is the
case this file pins: the record that lands inside SOMEONE ELSE'S reload window.

The harness is the same as the sibling test: a genuine second handle does a genuine atomic write,
timed to land inside the reloading handle's read. It is not a simulation of the interleaving.
"""
from __future__ import annotations

import contextlib
import json
import pathlib

import pytest

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk


def _texts(path) -> set:
    with open(path, encoding="utf-8") as fh:
        return {r.get("text") for r in json.load(fh)}


@pytest.fixture()
def json_store(tmp_path, monkeypatch):
    """A plain JSON store, whose save rewrites the whole file. The row store merges by id."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / "memory.json"
    seed = Inspeximus(path=str(path))
    seed._save_min_s = 0
    seed.remember("seed record", key="seed", mtype="fact")
    seed.flush()
    return path


def _reload_with_a_write_in_the_middle(handle, path, injected_text: str):
    """Call `handle.reload()` while a second handle writes inside its read."""
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
        handle.reload()
    finally:
        pathlib.Path.read_bytes = real_read
    assert fired["n"] == 1, "the injected write never ran, so this test measured nothing"


def test_a_write_that_lands_inside_a_reload_is_not_overwritten_by_the_reloading_handle(json_store):
    h = Inspeximus(path=str(json_store))
    h._save_min_s = 0
    _reload_with_a_write_in_the_middle(h, json_store, "written during the reload")
    # A refusal is a pass: it is what a signature taken before the read produces. The failure is
    # the call returning normally with the other writer's record gone.
    with contextlib.suppress(StoreChangedOnDisk):
        h.remember("written after the reload", key="after", mtype="fact")
        h.flush()
    on_disk = _texts(json_store)
    assert "written during the reload" in on_disk, (
        "the other writer was told its record was stored and it is not in the file. `reload()` read "
        "the file before that write and stamped its signature after it, so `_save` saw no change and "
        "rewrote the store from records that predate the write. On disk: %s" % sorted(on_disk))


def test_the_reloading_handle_still_persists_its_own_record(json_store):
    """The remedy must not turn the loss into a silent refusal of the caller's own write."""
    h = Inspeximus(path=str(json_store))
    h._save_min_s = 0
    _reload_with_a_write_in_the_middle(h, json_store, "written during the reload")
    try:
        h.remember("written after the reload", key="after", mtype="fact")
        h.flush()
    except StoreChangedOnDisk:
        h.reload()
        h.remember("written after the reload", key="after", mtype="fact")
        h.flush()
    on_disk = _texts(json_store)
    assert {"written during the reload", "written after the reload"} <= on_disk, sorted(on_disk)


def test_the_control_a_reload_with_no_competing_write_keeps_everything(json_store):
    """The arm that fails if the fixture stops reproducing the situation rather than if the store
    stops being correct."""
    other = Inspeximus(path=str(json_store))
    other._save_min_s = 0
    other.remember("written before the reload", key="other", mtype="fact")
    other.flush()
    h = Inspeximus(path=str(json_store))
    h._save_min_s = 0
    h.reload()
    h.remember("written after the reload", key="after", mtype="fact")
    h.flush()
    assert {"seed record", "written before the reload", "written after the reload"} <= _texts(json_store)
