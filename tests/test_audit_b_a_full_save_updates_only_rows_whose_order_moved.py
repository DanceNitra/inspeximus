"""AUDIT-B B-09: a full-diff row save must issue an order UPDATE only for rows whose order moved.

`sqlite_store.save` without `dirty` ends with `UPDATE records SET ord=? WHERE id=? AND ord<>?` for
every row that was neither added nor changed. The `ord<>?` makes the unmoved rows no-ops, but each one
is still a statement: a full reconcile of a 67,165-row store ran 67,165 of them, 1.18 s of
`executemany`, and `close_session` forces a full reconcile at every session boundary, so SessionStart
paid it on this project's hook store (measured 2026-09-27 on a copy).

The counter is the number of `UPDATE records SET ord` executions, read from SQLite's trace callback,
which fires once per row of an `executemany`. The control moves two rows and requires both the moved
order on disk and at least those two updates, so the counter cannot reach zero by skipping real work.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as ss  # noqa: E402

N = 300


@pytest.fixture
def updates(monkeypatch):
    box = [0]
    real = ss._connect

    def traced(path):
        con = real(path)
        con.set_trace_callback(lambda s: box.__setitem__(0, box[0] + 1)
                               if s.lstrip().upper().startswith("UPDATE RECORDS SET ORD") else None)
        return con

    monkeypatch.setattr(ss, "_connect", traced)
    return box


def _order_on_disk(p):
    con = sqlite3.connect(p)
    try:
        return [r[0] for r in con.execute("SELECT id FROM records ORDER BY ord")]
    finally:
        con.close()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-09: one no-op UPDATE per unmoved row on every full-diff save")
def test_a_full_save_updates_only_the_rows_that_moved(tmp_path, updates):
    p = str(tmp_path / "s.json")
    items = [{"id": f"id{i:04d}", "text": f"record {i}", "ts": 1.0, "mtype": "episodic"} for i in range(N)]
    snap = ss.save(p, items, {})["snapshot"]

    # CONTROL: move two rows. Their new order must land, through at least two updates.
    moved = list(items)
    moved[3], moved[7] = moved[7], moved[3]
    updates[0] = 0
    snap = ss.save(p, moved, snap)["snapshot"]
    if _order_on_disk(p) != [r["id"] for r in moved]:
        pytest.fail("control: the moved order did not reach the file")
    if updates[0] < 2:
        pytest.fail(f"control: {updates[0]} order updates for two moved rows")

    updates[0] = 0
    ss.save(p, moved, snap)
    assert _order_on_disk(p) == [r["id"] for r in moved]
    assert updates[0] == 0, f"{updates[0]} order UPDATE statements for {N} rows whose order did not move"
