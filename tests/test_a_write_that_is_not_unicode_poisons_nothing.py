"""A write whose text is not valid Unicode is refused, and poisons nothing after it (audit A-22).

A lone surrogate ("\\ud800") is legal in a Python str and legal in JSON ("\\ud800" is a valid escape, so any
MCP client can send one), and the hook's cp1250 stdin produces them (A-06). `remember` accepted such a
record and returned an id; then every save of the store raised UnicodeEncodeError, so every LATER write in
that handle was lost too, on both formats: measured, a good record written after the bad one was gone on
reopen. Through MCP the bad record stayed in the server's memory and made every recall that matched it
fail with "Error serializing to JSON" until the server restarted.

`remember` already refused an unserialisable `meta` for exactly this reason ("one poisoned record made every
subsequent _save() of the whole store fail"). The check now covers every string a record carries, and it
runs before the record exists.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus

BAD = "bad \ud800 text"


@pytest.fixture(params=["rows", "json"])
def store(request, tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", request.param)
    path = str(tmp_path / "m.json")
    m = Inspeximus(path)
    m.remember("an earlier good record", key="a", object="1")
    m.flush()
    return m, path


@pytest.mark.parametrize("field", ["text", "object", "key", "tags", "meta", "source", "session_id", "project"])
def test_a_record_that_is_not_unicode_is_refused_and_later_writes_persist(store, field):
    m, path = store
    kwargs = {"text": "a record", "key": "b", "object": "2"}
    kwargs.update({"text": BAD} if field == "text" else {"object": BAD} if field == "object" else
                  {"key": BAD} if field == "key" else {"tags": ["ok", BAD]} if field == "tags" else
                  {"meta": {"note": BAD}} if field == "meta" else {"source": {"doc": BAD}} if field == "source"
                  else {field: BAD})
    before = len(m.items)
    with pytest.raises(ValueError, match="not valid Unicode"):
        m.remember(**kwargs)
    assert len(m.items) == before, "the refused record entered memory anyway"
    m.remember("a later good record", key="c", object="3")
    m.flush()
    assert {r.get("key") for r in Inspeximus(path).items} >= {"a", "c"}, \
        "a write after the refused one did not persist"


def test_through_mcp_a_refused_write_leaves_recall_working(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    from _mcp_review import call, load_server
    mod = load_server(monkeypatch, tmp_path)
    r = call(mod, "remember", text="marker-7731 " + BAD)
    assert r.is_error, "the server reported success for a record it cannot store"
    call(mod, "remember", text="marker-7731 a readable fact")
    hits = call(mod, "recall", query="marker-7731", k=5)
    assert not hits.is_error, f"recall fails on a record the write refused: {hits.text[:120]}"
    assert any("readable fact" in (h.get("text") or "") for h in hits.data)
