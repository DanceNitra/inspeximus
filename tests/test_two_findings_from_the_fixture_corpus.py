"""Two defects found by opening 7,880 stores from the 20-day fixture corpus with 3.0.0 (2026-09-20).

The corpus is what the suite left in the user's Temp: every store it created since 2026-08-31, in
every state the tests produce. Opened blind, 482 refused to open and 4,035 failed verify_writes.
Almost all of that was the library being right (encrypted stores with no key, files that are not
stores, fixtures the tests corrupted on purpose, stores written with receipts off). Two were not.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def test_a_json_list_of_non_records_is_refused_with_a_reason_not_an_attribute_error():
    p = os.path.join(tempfile.mkdtemp(), "config.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]")
    with pytest.raises(ValueError, match="not records"):
        Inspeximus(path=p)
    assert open(p, encoding="utf-8").read() == "[1, 2, 3]"      # refused, so untouched


def test_a_vector_persisted_store_opened_without_the_embedder_is_not_reported_unpersisted():
    """117 of 7,880 corpus stores reported `store not persisted ... (differs in vec)` on open. They
    had been written with persist_vectors=True; the handle that verified them had no embedder and
    did not persist vectors, so its memory image carried no `vec` while the rows did. Nothing was
    unpersisted; the comparison compared a cache one side keeps and the other never loads."""
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    emb = lambda s: [float(len(s) % 7), 1.0, 0.5]                  # noqa: E731 -- a deterministic stand-in
    w = Inspeximus(path=p, embed=emb, persist_vectors=True, embed_id="stand-in")
    for i in range(3):
        w.remember(f"fact {i}", key=f"k{i}")
    w.flush()
    r = Inspeximus(path=p)                                        # no embedder, vectors not persisted
    ok, problems = r.verify_writes()
    assert not [x for x in problems if "store not persisted" in x], problems
    # the control: an edit that really has not reached disk is still reported
    r.items[0]["text"] = "edited in memory only"
    r._dirty = False
    ok, problems = r.verify_writes()
    assert any("store not persisted" in x and "text" in x for x in problems), problems
