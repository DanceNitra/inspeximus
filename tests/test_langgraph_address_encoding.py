"""Two addresses that differ must not resolve to the same record — measured against LangGraph's own store.

`InspeximusStore` is sold as a drop-in `BaseStore`. Its record id was built by joining the namespace with
"/" and appending the key after "::" — but both separators are legal INSIDE a namespace element and inside
a key, so distinct addresses collapsed onto one id. The namespace is LangGraph's per-user boundary, so
where element 0 is a user id this is one user reading and overwriting another's record.

MEASURED before the fix, with the reference InMemoryStore as the control (it keeps all of these apart):

    put(("a","b"), "k", A); put(("a/b",), "k", B)      -> both get() returned B
    put(("u1",), "b::k", A); put(("u1::b",), "k", B)   -> both get() returned B
    search(("user1",)) with user1 and user10 present   -> returned BOTH (reference: only user1)

The third is a separate defect with the same shape: the search scope was a string prefix, and "lg::user1"
is a string prefix of "lg::user10::notes". A tenant-scoped search returned another tenant's records.

Every assertion below is a PARITY assertion — ours must equal the reference — so these tests cannot be
satisfied by a store that simply refuses everything.
"""
import os

import pytest

pytest.importorskip("langgraph", reason="the LangGraph adapter needs langgraph")
from langgraph.store.memory import InMemoryStore                      # noqa: E402

from inspeximus.integrations.langgraph import InspeximusStore         # noqa: E402


@pytest.fixture()
def ours(tmp_path):
    return InspeximusStore(str(tmp_path / "s.json"))


@pytest.fixture()
def reference():
    return InMemoryStore()


def _put_pair(store, ns1, k1, ns2, k2):
    store.put(ns1, k1, {"who": "A"})
    store.put(ns2, k2, {"who": "B"})
    g1, g2 = store.get(ns1, k1), store.get(ns2, k2)
    return (g1.value if g1 else None), (g2.value if g2 else None)


ADDRESS_PAIRS = [
    pytest.param(("a", "b"), "k", ("a/b",), "k", id="nested-ns-vs-slash-in-element"),
    pytest.param(("u1",), "b::k", ("u1::b",), "k", id="separator-inside-the-key"),
    pytest.param(("a/b",), "k", ("a%2Fb",), "k", id="a-literal-percent-escape-is-not-the-escape"),
    pytest.param(("users", "u1"), "notes", ("users", "u2"), "notes", id="control-ordinary-sibling-tenants"),
]


@pytest.mark.parametrize("ns1,k1,ns2,k2", ADDRESS_PAIRS)
def test_distinct_addresses_stay_distinct_exactly_as_the_reference_does(ours, reference, ns1, k1, ns2, k2):
    assert _put_pair(reference, ns1, k1, ns2, k2) == ({"who": "A"}, {"who": "B"}), (
        "fixture error: the reference store itself conflated these, so there is nothing to match")
    assert _put_pair(ours, ns1, k1, ns2, k2) == ({"who": "A"}, {"who": "B"}), (
        f"{ns1}+{k1!r} and {ns2}+{k2!r} resolved to the same record. Where element 0 is a user id, that is "
        f"one user reading another's data through a store advertised as a drop-in replacement.")


def _scope(store):
    store.put(("user1",), "notes", {"who": "user1"})
    store.put(("user10",), "notes", {"who": "user10"})
    store.put(("user1", "deep"), "n", {"who": "user1-deep"})
    return (sorted((h.value or {}).get("who") for h in store.search(("user1",))),
            sorted((h.value or {}).get("who") for h in store.search(())))


def test_a_tenant_scoped_search_does_not_return_a_neighbour_with_a_shared_string_prefix(ours, reference):
    """The scope is a segment prefix, not a string prefix. user1 must not see user10.

    The second half is the control: a DEEPER namespace under user1 must still be included, or the fix has
    made the scope useless rather than correct.
    """
    ref_scoped, ref_all = _scope(reference)
    our_scoped, our_all = _scope(ours)

    assert "user1-deep" in ref_scoped and "user10" not in ref_scoped, ("fixture error", ref_scoped)
    assert our_scoped == ref_scoped, (
        f"search scoped to ('user1',) returned {our_scoped}; the reference returns {ref_scoped}")
    assert our_all == ref_all, ("an empty prefix must still match everything", our_all, ref_all)


def test_an_ordinary_key_encodes_exactly_as_before_so_existing_stores_still_resolve(ours):
    """The no-migration claim, pinned. Escaping only moves components that were already ambiguous."""
    assert InspeximusStore._mkey(("users", "u1"), "notes") == "lg::users/u1::notes"
    assert InspeximusStore._esc("notes") == "notes"


def test_the_raw_subject_string_is_COARSE_and_erase_namespace_is_the_precise_path(ours):
    """The honest scope of the erasure surface, pinned so it cannot be quietly forgotten.

    `source.doc` is "lg::" + "::".join(namespace), which is lossy: ("a","b") and ("a::b",) are the SAME
    subject string, so forget_subject on it takes both -- measured, 2 of 2, and erasure is irreversible.

    Escaping the subject the way the record id is escaped is NOT available, and the measurements are why:
    it changes the subject for every record already written, so a DSAR for a namespace containing "/"
    matched nothing (erased:0, record still active, certificate still issued); and rewriting stored
    records to the new subject breaks their write receipts ("stored content no longer matches its write
    receipt"), which is a tamper-evident store falsifying its own evidence.

    So the coarse path stays coarse and compatible, and erase_namespace() is exact.
    """
    ours.put(("a", "b"), "k", {"who": "tenant-a-b"})
    ours.put(("a::b",), "k", {"who": "tenant-a-colons-b"})
    ours.put(("other",), "k", {"who": "untouched"})

    precise = ours.erase_namespace(("a", "b"))
    survivors = sorted((r.get("meta") or {}).get("value", {}).get("who", "?")
                       for r in ours.store.items if r.get("status") == "active")
    assert precise["forgotten"] == 1, precise
    assert survivors == ["tenant-a-colons-b", "untouched"], survivors

    # and the coarse path is coarse -- documented, not fixed
    ours.put(("a", "b"), "k", {"who": "tenant-a-b"})
    coarse = ours.store.forget_subject("lg::a::b")
    assert coarse["erased"] == 2, (
        "the raw-subject path stopped being coarse -- if that was deliberate, this docstring and "
        "erase_namespace's are now wrong; if not, the subject encoding changed and legacy stores broke")


def test_erase_namespace_reaches_a_legacy_record_whose_id_predates_escaping(tmp_path):
    """The regression this whole rework exists to prevent: data written before the fix stays erasable."""
    m = InspeximusStore(str(tmp_path / "legacy.json"))
    m.put(("users", "u/1"), "notes", {"pii": "alice"})       # "/" in a namespace element
    m.put(("users", "u2"), "notes", {"pii": "bob"})

    assert m.get(("users", "u/1"), "notes").value == {"pii": "alice"}, "the read path lost a legacy row"
    r = m.erase_namespace(("users", "u/1"))
    assert r["forgotten"] == 1, r
    left = sorted((x.get("meta") or {}).get("value", {}).get("pii", "?")
                  for x in m.store.items if x.get("status") == "active")
    assert left == ["bob"], left


def test_erase_namespace_can_take_the_subtree_when_asked_and_not_otherwise(tmp_path):
    m = InspeximusStore(str(tmp_path / "tree.json"))
    m.put(("users", "u1"), "notes", {"v": 1})
    m.put(("users", "u1", "deep"), "n", {"v": 2})

    assert m.erase_namespace(("users", "u1"))["forgotten"] == 1, "exact must not take the child"
    assert m.erase_namespace(("users", "u1"), include_children=True)["forgotten"] == 1
    assert [x for x in m.store.items if x.get("status") == "active"] == []


def test_an_ordinary_namespace_keeps_its_subject_string_so_a_pending_dsar_still_matches(ours):
    """Backward compatibility for the erasure path specifically: only ambiguous namespaces move."""
    ours.put(("users", "u1"), "notes", {"v": 1})
    assert [r.get("source", {}).get("doc") for r in ours.store.items] == ["lg::users::u1"]


def test_the_round_trip_the_adapter_exists_for_is_untouched(ours):
    """Put/overwrite/history/list/delete, so the encoding change cannot have broken the ordinary path."""
    ours.put(("users", "u1"), "notes", {"v": 1})
    ours.put(("users", "u1"), "notes", {"v": 2})
    assert ours.get(("users", "u1"), "notes").value == {"v": 2}
    assert ours.history(("users", "u1"), "notes") == [{"v": 1}, {"v": 2}]
    assert ours.list_namespaces() == [("users", "u1")]
    ours.put(("users", "u1"), "notes", None)
    assert ours.get(("users", "u1"), "notes") is None
