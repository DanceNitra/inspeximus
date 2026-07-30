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


def test_an_erasure_for_one_namespace_does_not_hard_delete_another(ours):
    """The same lossy join was the DSAR SUBJECT, and erasure is irreversible.

    `source.doc` was "lg::" + "::".join(namespace), so ("a","b") and ("a::b",) were literally the same
    subject string. MEASURED before the fix: forget_subject("lg::a::b") erased 2 of 2 and only the
    unrelated record survived. Unlike a bad read, this one cannot be undone.
    """
    ours.put(("a", "b"), "k", {"who": "tenant-a-b"})
    ours.put(("a::b",), "k", {"who": "tenant-a-colons-b"})
    ours.put(("other",), "k", {"who": "untouched"})

    res = ours.store.forget_subject("lg::a::b")
    survivors = sorted((r.get("meta") or {}).get("value", {}).get("who", "?") for r in ours.store.items)

    assert res["erased"] == 1, (
        f"a DSAR naming one namespace erased {res['erased']} records -- it reached into a namespace the "
        f"request did not name, and erasure is irreversible")
    assert survivors == ["tenant-a-colons-b", "untouched"], survivors


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
