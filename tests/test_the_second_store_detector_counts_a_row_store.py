"""The two-store detector must see a ROW store, because that is the store the defect produced.

MEASURED 2026-10-28 on the machine that actually had the defect, installing the Hermes MCP catalog
entry fresh. The provider's store was a row store named `memory.json`; `_store_record_count` read it
as JSON, raised `UnicodeDecodeError` on the SQLite header, returned 0, and `other_stores` dropped it
as an empty file. The detector written to report two stores reported none, on the one case it exists
for.

AND THE BLIND SPOT WAS THE COMMON CASE, not an exotic one. `Inspeximus._rows_available` writes a NEW
store as rows and nobody chooses it, so almost every store a user has today is a row store. The
3.25.0 tests passed because their fixtures wrote JSON with `json.dump`: a fixture that does not
reproduce the real format is a control that cannot fail. Every fixture here is written by the
library.

A store's name says nothing about its format, so the header decides. The counting path is read-only
on purpose: `sqlite_store._connect` creates the schema and stamps `meta` on a file it finds fresh,
and this reads a store that belongs to another program.

The controls are the point: a JSON store still counts, an EMPTY row store is still left out, and the
row store is not reported against itself.
"""
from __future__ import annotations

import json
import os

from inspeximus._surface import _store_record_count, other_stores


def _row_store(path, texts):
    """A row store written by the library itself, so the test cannot drift from the real format."""
    from inspeximus import Inspeximus
    os.makedirs(os.path.dirname(path), exist_ok=True)
    m = Inspeximus(str(path))          # a new store is written as rows; nobody chooses it
    for t in texts:
        m.remember(t)
    # `remember` persists; there is no separate save on this surface.
    return path


def _json_store(path, texts):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([{"id": "m%d" % i, "text": t, "type": "fact"} for i, t in enumerate(texts)], fh)
    return path


def test_a_row_store_is_counted(tmp_path):
    p = _row_store(str(tmp_path / "rows" / "memory.json"), ["The deploy target is prod-7"])
    with open(p, "rb") as fh:
        assert fh.read(16) == b"SQLite format 3\x00", "the fixture is not a row store, so this test proves nothing"
    assert _store_record_count(p) == 1, (
        "a row store read 0 records, which is how the detector went blind on the real machine")


def test_a_hermes_row_store_is_named_when_the_server_reads_another_file(tmp_path):
    hermes = tmp_path / "hermes_home"
    other = _row_store(str(hermes / "inspeximus" / "memory.json"), ["The deploy target is prod-7"])
    in_use = _json_store(str(tmp_path / "cwd" / "inspeximus_memory.json"), [])

    found = other_stores(in_use, env={"HERMES_HOME": str(hermes)}, home=str(tmp_path))
    paths = [f["path"] for f in found]
    assert os.path.realpath(other) in paths, (
        "the provider's ROW store holds a record, the server reads another file, and nothing named "
        "it. Found: %r" % (found,))
    named = [f for f in found if f["path"] == os.path.realpath(other)][0]
    assert named["memories"] == 1, named


def test_control_a_json_store_still_counts(tmp_path):
    p = _json_store(str(tmp_path / "j" / "memory.json"), ["one", "two"])
    assert _store_record_count(p) == 2


def test_control_an_empty_row_store_is_not_reported(tmp_path):
    hermes = tmp_path / "hermes_home"
    empty = _row_store(str(hermes / "inspeximus" / "memory.json"), [])
    in_use = _json_store(str(tmp_path / "cwd" / "inspeximus_memory.json"), ["a"])
    assert _store_record_count(empty) == 0, "an empty row store must count 0, not raise"
    found = other_stores(in_use, env={"HERMES_HOME": str(hermes)}, home=str(tmp_path))
    assert found == [], "a store with no records is not a memory anyone is missing: %r" % (found,)


def test_control_the_row_store_in_use_is_not_reported_against_itself(tmp_path):
    hermes = tmp_path / "hermes_home"
    p = _row_store(str(hermes / "inspeximus" / "memory.json"), ["The deploy target is prod-7"])
    found = other_stores(p, env={"HERMES_HOME": str(hermes)}, home=str(tmp_path))
    assert found == [], "the store in use was reported as another store: %r" % (found,)


def test_control_reading_a_row_store_does_not_change_it(tmp_path):
    """The counter must not write: it reads a file another program owns."""
    p = _row_store(str(tmp_path / "rows" / "memory.json"), ["The deploy target is prod-7"])
    before = open(p, "rb").read()
    assert _store_record_count(p) == 1
    assert open(p, "rb").read() == before, "counting a row store modified it"
