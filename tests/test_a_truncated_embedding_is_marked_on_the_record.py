"""A record embedded from the start of its text only carries a mark, and `reembed` names it (3.18, AUDIT-A F-1).

`embed_fitting` sends an input over the model's context again shortened to 0.75 of its length (8e8fc05b). AUDIT-A
found the cut silent: the record carried no field, the recipe tag was unchanged, and `reembed` reported nothing. Now the
vector carries `truncated_to`, `_set_vec` copies it to `meta["embed_truncated"]` (the characters kept), a later complete
vector removes it, and `reembed` returns `truncated: [{id, kept, of}]`. The mark is a `meta` key other than `context`,
so writing it drops none of the record's index caches.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from inspeximus import _http  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402
from test_an_input_over_the_context_is_embedded_by_its_prefix import LIMIT, TEXT, _Endpoint, _env, _fit  # noqa: E402,F401

FORMATS = ["json", "db"]
HEADERS = {"json": b"[", "db": b"SQLite format 3"}   # the whole-file JSON format is a list


@pytest.fixture
def endpoint():
    e = _Endpoint("413")
    yield e
    e.close()


def _embedder(url):
    return lambda t: _http.embed_fitting(url, "m", t, "", None, 10)


def _open(tmp_path, fmt, monkeypatch, embed=None):
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")       # pins a new store to the whole-file format
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    p = str(tmp_path / ("s." + fmt))
    return p, Inspeximus(path=p, embed=embed)


def test_embed_fitting_says_how_much_it_kept(endpoint):
    v = _http.embed_fitting(endpoint.url, "m", TEXT, "", None, 10)
    assert v.truncated_to == _fit(len(TEXT)) and v[0] == float(v.truncated_to)
    whole = _http.embed_fitting(endpoint.url, "m", TEXT[:LIMIT], "", None, 10)
    assert whole.truncated_to is None, "a vector of the whole text was marked as cut"


@pytest.mark.parametrize("fmt", FORMATS)
def test_remember_marks_a_cut_record_and_the_mark_is_stored(tmp_path, endpoint, monkeypatch, fmt):
    p, m = _open(tmp_path, fmt, monkeypatch, _embedder(endpoint.url))
    cut = m.remember(TEXT)
    whole = m.remember("a short note")
    m.flush()
    with open(p, "rb") as fh:
        assert fh.read(16).startswith(HEADERS[fmt]), "CONTROL: the store is in the %s format" % fmt
    rows = {r["id"]: r for r in Inspeximus(path=p)._items}
    assert rows[cut]["meta"].get("embed_truncated") == _fit(len(TEXT)), rows[cut]["meta"]
    assert "embed_truncated" not in (rows[whole].get("meta") or {})


@pytest.mark.parametrize("fmt", FORMATS)
def test_reembed_names_the_cut_records_and_a_complete_vector_removes_the_mark(tmp_path, endpoint, monkeypatch, fmt):
    p, m = _open(tmp_path, fmt, monkeypatch)
    cut = m.remember(TEXT)
    m.remember("a short note")
    m.embed = _embedder(endpoint.url)
    out = m.reembed(only_missing=False)
    assert out["truncated"] == [{"id": cut, "kept": _fit(len(TEXT)), "of": len(TEXT)}], out
    m.embed = lambda t: [1.0, 0.0, 0.5]                         # an endpoint that takes the whole text
    out = m.reembed(only_missing=False)
    assert "truncated" not in out, out
    m.flush()
    rows = {r["id"]: r for r in Inspeximus(path=p)._items}
    assert "embed_truncated" not in (rows[cut].get("meta") or {}), "a complete vector left the mark"


def test_a_plain_list_from_a_callers_embedder_marks_nothing(tmp_path, monkeypatch):
    p, m = _open(tmp_path, "db", monkeypatch, lambda t: [1.0, 0.0, 0.5])
    rid = m.remember(TEXT)
    assert "embed_truncated" not in (next(r for r in m._items if r["id"] == rid).get("meta") or {})


@pytest.mark.parametrize("fmt", ["db"])
def test_writing_the_mark_keeps_the_records_index_caches(tmp_path, monkeypatch, fmt):
    """The row store tracks edits; the mark is `meta.embed_truncated`, which the index does not read."""
    p, m = _open(tmp_path, fmt, monkeypatch)
    m.remember(TEXT)
    m.flush()
    h = Inspeximus(path=p)
    h._ix_held = True
    rec = h._items[0]
    h._rec_tokens(rec)
    assert rec["id"] in h._tok_cache, "CONTROL: the record's tokens are cached"
    rev = h._rev
    v = _http.Embedding([1.0, 0.0, 0.5])
    v.truncated_to = 100
    h._set_vec(rec, v)
    assert h._rev > rev and rec["meta"]["embed_truncated"] == 100, "CONTROL: the mark was written as an edit"
    assert rec["id"] in h._tok_cache, "writing the mark dropped the record's cached tokens"
