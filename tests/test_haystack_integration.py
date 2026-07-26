"""The Haystack DocumentStore adapter — 8 functions with no executed body line, `write_documents` the
largest single uncovered function anywhere in the package (31 body lines).

The module sells itself as "a faithful, drop-in replacement for `InMemoryDocumentStore` ... whose delete
removes the value from the bytes on disk rather than dropping a reference". Both halves are testable and
both are tested here: the parity claim against the REAL `InMemoryDocumentStore`, and the on-disk claim by
reading the raw file after a delete.

Guarded with `importorskip` — CI's base environment has no haystack.
"""
import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("haystack")

from haystack import Document                                      # noqa: E402
from haystack.document_stores.types import DuplicatePolicy         # noqa: E402

from inspeximus.integrations.haystack import InspeximusDocumentStore  # noqa: E402


def _path():
    return os.path.join(tempfile.mkdtemp(), "docs.json")


def _docs():
    return [Document(content="the invoice is due in March", meta={"kind": "invoice", "year": 2026}),
            Document(content="the contract renews in June", meta={"kind": "contract", "year": 2026}),
            Document(content="the invoice was paid in April", meta={"kind": "invoice", "year": 2025})]


def _both():
    """Ours and the reference, so parity claims are measured rather than asserted."""
    from haystack.document_stores.in_memory import InMemoryDocumentStore
    return [("reference", InMemoryDocumentStore()), ("inspeximus", InspeximusDocumentStore(path=_path()))]


# ── parity with the store it replaces ───────────────────────────────────────────────────────────────
def test_write_and_count_match_the_reference():
    for name, store in _both():
        n = store.write_documents(_docs())
        assert n == 3, f"{name}: write_documents must report what it wrote, got {n}"
        assert store.count_documents() == 3, name


def test_filter_documents_matches_the_reference():
    flt = {"field": "meta.kind", "operator": "==", "value": "invoice"}
    got = {}
    for name, store in _both():
        store.write_documents(_docs())
        got[name] = sorted(d.content for d in store.filter_documents(flt))
    assert got["inspeximus"] == got["reference"], got
    assert len(got["inspeximus"]) == 2, got


def test_filter_with_no_filters_returns_everything():
    for name, store in _both():
        store.write_documents(_docs())
        assert len(store.filter_documents()) == 3, name


def test_delete_documents_matches_the_reference():
    for name, store in _both():
        store.write_documents(_docs())
        victim = store.filter_documents()[0]
        store.delete_documents([victim.id])
        remaining = [d.id for d in store.filter_documents()]
        assert victim.id not in remaining, name
        assert len(remaining) == 2, name


def test_deleting_an_unknown_id_does_not_explode():
    store = InspeximusDocumentStore(path=_path())
    store.write_documents(_docs())
    store.delete_documents(["no-such-id"])
    assert store.count_documents() == 3


# ── the claim the adapter is FOR ────────────────────────────────────────────────────────────────────
def test_erase_removes_the_content_from_the_bytes_on_disk():
    """"...whose delete removes the value from the bytes on disk rather than dropping a reference." That
    is the whole pitch versus an in-memory store, and it is checked by reading the raw file."""
    p = _path()
    store = InspeximusDocumentStore(path=p)
    store.write_documents([Document(content="alice@example.com signed the NDA", meta={"kind": "pii"})])
    victim = store.filter_documents()[0]

    raw_before = io.open(p, encoding="utf-8").read()
    assert "alice@example.com" in raw_before, "precondition: the value must be on disk to begin with"

    res = store.erase_documents([victim.id], request_id="DSAR-1")

    raw_after = io.open(p, encoding="utf-8").read()
    assert "alice@example.com" not in raw_after, \
        f"erase must remove the value from the file, not just drop a reference: {res}"
    assert store.count_documents() == 0


def test_the_documents_survive_a_reopen():
    """The other half of the pitch: persists to a file instead of a process-lifetime dict."""
    p = _path()
    InspeximusDocumentStore(path=p).write_documents(_docs())
    assert InspeximusDocumentStore(path=p).count_documents() == 3


# ── serialisation, which Haystack pipelines rely on ─────────────────────────────────────────────────
def test_to_dict_and_from_dict_round_trip():
    p = _path()
    store = InspeximusDocumentStore(path=p)
    store.write_documents(_docs())

    d = store.to_dict()
    assert isinstance(d, dict) and d, d
    json.dumps(d)                                    # a pipeline serialises this to YAML/JSON

    revived = InspeximusDocumentStore.from_dict(d)
    assert revived.count_documents() == 3, "a revived store must see the same documents"


def test_duplicate_policy_overwrite_does_not_multiply_documents():
    store = InspeximusDocumentStore(path=_path())
    doc = Document(id="fixed-id", content="version one", meta={"kind": "note"})
    store.write_documents([doc], policy=DuplicatePolicy.OVERWRITE)
    store.write_documents([Document(id="fixed-id", content="version two", meta={"kind": "note"})],
                          policy=DuplicatePolicy.OVERWRITE)

    docs = store.filter_documents()
    assert len(docs) == 1, f"OVERWRITE must replace, not append: {[d.content for d in docs]}"
    assert docs[0].content == "version two", docs[0].content


def test_count_ignores_records_that_are_not_haystack_documents():
    """The store is a general inspeximus store and a caller may hold other memories in it. Counting raw
    rows instead of documents inflates `count_documents` and breaks every pipeline that pages on it.

    In a store where every row IS a document the two implementations agree, which is why a mutation that
    counts raw rows survived the parity tests: the fixture could not tell them apart."""
    from inspeximus import Inspeximus

    backing = Inspeximus(path=_path())
    backing.remember("an ordinary memory that is not a haystack document")
    store = InspeximusDocumentStore(store=backing)

    store.write_documents(_docs())

    assert store.count_documents() == 3, "the non-document row must not be counted"
    assert len(store.filter_documents()) == 3, "nor returned"
    assert len(list(backing.items)) >= 4, "but it must still be in the underlying store"


@pytest.mark.parametrize("bad", ["not a list", [{"content": "a dict is not a Document"}], [None], 42])
def test_write_documents_rejects_anything_that_is_not_a_list_of_documents(bad):
    """The guard exists and had no test, so replacing `raise ValueError` with `return 0` survived: a
    pipeline handing in the wrong shape would have been told "wrote 0" and carried on."""
    store = InspeximusDocumentStore(path=_path())
    with pytest.raises(ValueError):
        store.write_documents(bad)
    assert store.count_documents() == 0, "and nothing may be written on the way to refusing"
