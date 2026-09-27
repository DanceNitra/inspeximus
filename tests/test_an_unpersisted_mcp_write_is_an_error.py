"""A write the store could not persist reaches an MCP client as an error (audit A-22, session 1).

The write tools reported a failed save as `persisted: false` inside a result whose `isError` was false.
A client that reads only the error flag, which is what most agent loops do, believed the write had
landed. `_write_verdict`, which six write tools share, now raises when the save failed, naming the id and
the error; the record stays in memory and the next save retries it, as before.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("mcp")
from _mcp_review import call, load_server


def _disk_full(monkeypatch):
    import inspeximus.core as core

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(core, "_durable_replace", boom)


def test_a_write_that_saved_is_not_an_error(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_STORE_FORMAT="json")
    r = call(mod, "remember", text="the region is frankfurt")
    assert not r.is_error and r.data.get("persisted") is True, r


def test_a_write_the_store_could_not_persist_is_an_error(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_STORE_FORMAT="json")
    call(mod, "remember", text="a first record so the store file exists")
    _disk_full(monkeypatch)
    r = call(mod, "remember", text="the region is frankfurt")
    if not r.is_error and r.data.get("persisted") is not False:
        pytest.fail(f"control: the forced save failure was not seen at all: {r.data}")
    assert r.is_error, f"a write that did not persist came back as a success: {r.data}"
    assert "not persisted" in r.text and "No space left" in r.text, r.text


def test_a_keyed_decision_that_did_not_persist_is_an_error_too(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_STORE_FORMAT="json")
    call(mod, "remember", text="a first record so the store file exists")
    _disk_full(monkeypatch)
    r = call(mod, "remember_decision", decision="use Postgres for the ledger", because="joins", topic="db")
    assert r.is_error, f"remember_decision reported success for a write that did not persist: {r.data}"
