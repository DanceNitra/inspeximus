"""3.5.2: a write that never reached disk says so where the caller looks, and a foreign SQLite lock
is retried a bounded number of times and named when it wins.

Crew OS, 2026-09-22, on the live 23.7 MB row store with 14 processes open on it: remember()
returned an id, `last_write` read `blocked: False`, `_persist_error` held "OperationalError:
database is locked", and the record was not on disk. Five attempts in a row failed, the sixth
landed. The writer holds the inter-process store lock, so a SQLite lock at that moment belongs
to a client outside it. Reproduced here with a raw sqlite3 connection holding BEGIN IMMEDIATE.
"""
import os
import sqlite3
import threading
import time

import pytest

from inspeximus import Inspeximus, sqlite_store


def _rows(path):
    return Inspeximus(path)._rows_available()


@pytest.fixture
def short_busy(monkeypatch):
    monkeypatch.setattr(sqlite_store, "BUSY_TIMEOUT_S", 0.2)


def _hold(path, seconds, started):
    con = sqlite3.connect(path, timeout=5, isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    started.set()
    time.sleep(seconds)
    con.execute("ROLLBACK")
    con.close()


def test_a_foreign_lock_that_outlasts_the_retries_is_reported_on_last_write_and_the_result(tmp_path, short_busy, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_SAVE_RETRIES", "1")
    path = str(tmp_path / "s.json")
    m = Inspeximus(path)
    m.remember("seed", key="crew::seed", object="v1")
    m.flush()
    if not _rows(path):
        pytest.skip("row store only")
    started = threading.Event()
    t = threading.Thread(target=_hold, args=(path, 3.0, started), daemon=True)
    t.start()
    started.wait(5)
    rid = m.remember("layer", key="crew::L", object="v1")
    lw = m.last_write
    assert lw["id"] == rid and lw["blocked"] is False
    assert lw["persisted"] is False, lw
    assert "locked" in lw["persist_error"] and "outside inspeximus" in lw["persist_error"], lw["persist_error"]
    assert "2 attempt(s)" in lw["persist_error"]
    res = m.retire("crew::seed", reason="x")
    assert res["persisted"] is False and "locked" in res["persist_error"]
    t.join()
    # the record is still in memory, and the explicit call lands it once the holder is gone
    m.flush()
    assert m.last_write["persisted"] is True and "persist_error" not in m.last_write
    fresh = Inspeximus(path)
    assert fresh.current("crew::L")["text"] == "layer"
    assert fresh.current("crew::seed") is None


def test_a_foreign_lock_that_clears_within_the_retries_costs_nothing_but_time(tmp_path, short_busy, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_SAVE_RETRIES", "2")
    path = str(tmp_path / "s.json")
    m = Inspeximus(path)
    m.remember("seed", key="crew::seed", object="v1")
    m.flush()
    if not _rows(path):
        pytest.skip("row store only")
    started = threading.Event()
    t = threading.Thread(target=_hold, args=(path, 0.4, started), daemon=True)
    t.start()
    started.wait(5)
    m.remember("layer", key="crew::L", object="v1")
    assert m.last_write["persisted"] is True, m.last_write
    assert m._persist_error is None
    t.join()
    assert Inspeximus(path).current("crew::L")["text"] == "layer"


def test_an_unlocked_store_reports_persisted_true_and_no_error_field(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"))
    m.remember("plain", key="crew::P", object="p")
    assert m.last_write["persisted"] is True and "persist_error" not in m.last_write
    assert m.retire("crew::P", reason="done")["persisted"] is True


def test_the_store_lock_key_ignores_the_case_of_the_path(tmp_path):
    from inspeximus.core import _StoreLock
    a = _StoreLock(str(tmp_path / "Store.json"))._path
    b = _StoreLock(str(tmp_path / "store.JSON"))._path
    if os.path.normcase("A") == "a":
        assert a == b, "two spellings of one path on a case-insensitive filesystem must share a lock"
    else:
        assert a != b


def test_the_mcp_write_result_carries_persisted(tmp_path, monkeypatch, short_busy):
    pytest.importorskip("mcp")
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("INSPEXIMUS_SAVE_RETRIES", "0")
    srv = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    ok = srv.remember("seed", key="crew::seed", object="v1")
    assert ok["persisted"] is True and "persist_error" not in ok
    if not _rows(str(tmp_path / "mcp.json")):
        pytest.skip("row store only")
    started = threading.Event()
    t = threading.Thread(target=_hold, args=(str(tmp_path / "mcp.json"), 1.5, started), daemon=True)
    t.start()
    started.wait(5)
    bad = srv.remember("layer", key="crew::L", object="v1")
    assert bad["persisted"] is False and "locked" in bad["persist_error"], bad
    t.join()


def test_the_cli_exits_4_when_the_store_could_not_persist(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = str(tmp_path / "cli.json")
    m = Inspeximus(path)
    m.remember("seed", key="crew::seed", object="v1")
    m.flush()
    if not _rows(path):
        pytest.skip("row store only")
    env = {**os.environ, "INSPEXIMUS_SAVE_RETRIES": "0", "INSPEXIMUS_BUSY_TIMEOUT_S": "0.2"}
    started = threading.Event()
    t = threading.Thread(target=_hold, args=(path, 4.0, started), daemon=True)
    t.start()
    started.wait(5)
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", path, "--json", "remember", "layer",
                        "--key", "crew::L", "--object", "v1"],
                       capture_output=True, text=True, cwd=root, encoding="utf-8", env=env, timeout=60)
    t.join()
    assert r.returncode == 4, (r.returncode, r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert out["persisted"] is False and "locked" in out["persist_error"]
    assert "NOT PERSISTED" in r.stderr
