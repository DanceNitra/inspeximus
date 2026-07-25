"""`infer_lineage` — stamp a derivation edge from what the store can see, with no flag from the writer.

Why it exists: the flagged path (`remember(derived=True)`) measured **0.00% over 27,290 writes** in a real
43-day, 8-agent deployment we wrote ourselves. A mechanism that requires the writer to opt in does not run.

Why it is null-adjusted: on 27,342 real agent writes a RAW overlap threshold is degenerate — median overlap
against the true predecessor is 1.000 and a 0.8 threshold still stamps 77%, because agents reuse a small
vocabulary. The tests below pin that behaviour rather than describing it.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


def _parents(m, rid):
    return next(r for r in m.items if r["id"] == rid).get("derived_from")


def test_off_by_default_changes_nothing():
    """A shipped write path must not change silently. Default is 0.0 = off."""
    m = _store()
    assert m.infer_lineage == 0.0
    m.remember("the billing api authenticates with oauth2 tokens issued by keycloak")
    m.recall("billing api authentication")
    rid = m.remember("summary: billing authenticates via oauth2 tokens from keycloak")
    assert _parents(m, rid) is None


def test_a_derivative_is_stamped_with_no_flag():
    m = _store(infer_lineage=0.2)
    parent = m.remember("the billing api authenticates with oauth2 tokens issued by keycloak")
    m.remember("the office coffee machine needs descaling every autumn")
    m.recall("billing api authentication")
    rid = m.remember("summary: billing authenticates via oauth2 tokens from keycloak")
    assert _parents(m, rid) == [parent], "the store should carry the edge without being told"


def test_an_unrelated_write_after_the_same_recall_is_not_stamped():
    m = _store(infer_lineage=0.2)
    m.remember("the billing api authenticates with oauth2 tokens issued by keycloak")
    m.recall("billing api authentication")
    rid = m.remember("the office coffee machine needs descaling every autumn")
    assert _parents(m, rid) is None


def test_vocabulary_alone_does_not_earn_an_edge():
    """THE regression that made the raw version unusable: in a corpus where every record reuses the same
    words, a raw overlap threshold stamps almost everything. Only the part above the store's own baseline
    should count."""
    m = _store(infer_lineage=0.2)
    for i in range(30):
        m.remember(f"billing api note {i} about oauth2 tokens keycloak authentication service")
    m.remember("the billing api authenticates with oauth2 tokens issued by keycloak for tenant acme")
    m.recall("tenant acme keycloak")

    derived = m.remember("summary: tenant acme billing authenticates via oauth2 tokens from keycloak")
    vocab_only = m.remember("billing api note about oauth2 tokens keycloak authentication service")

    assert _parents(m, derived), "a genuine derivative must still be caught in a repetitive corpus"
    assert _parents(m, vocab_only) is None, "shared vocabulary alone must not earn a lineage edge"


def test_an_explicit_parent_always_wins():
    m = _store(infer_lineage=0.2)
    a = m.remember("first fact about oauth2 keycloak billing tokens")
    b = m.remember("second fact about oauth2 keycloak billing tokens")
    m.recall("oauth2 keycloak billing")
    rid = m.remember("summary of oauth2 keycloak billing tokens", derived_from=[a])
    assert _parents(m, rid) == [a], "inference must never override what the caller declared"
    assert b not in (_parents(m, rid) or [])


def test_a_stamped_write_is_reachable_from_provenance():
    """The edge is only worth stamping if a correction can walk it."""
    m = _store(infer_lineage=0.2, receipts=True)
    parent = m.remember("the billing api authenticates with oauth2 tokens issued by keycloak",
                        source={"doc": "adr-014"})
    m.recall("billing api authentication")
    rid = m.remember("summary: billing authenticates via oauth2 tokens from keycloak")

    p = m.provenance(id=rid)
    assert p["origin"]["derived"] is True
    assert parent in p["origin"]["ancestors"]
    assert "adr014" in p["origin"]["inherited_taint"], "taint must ride the inferred edge"


def test_short_text_refuses_to_guess():
    m = _store(infer_lineage=0.2)
    m.remember("the billing api authenticates with oauth2 tokens issued by keycloak")
    m.recall("billing api")
    rid = m.remember("oauth2 yes")            # too few content words to judge
    assert _parents(m, rid) is None


def test_overlap_is_asymmetric_and_drops_stopwords():
    o = Inspeximus._overlap
    assert o("the billing api uses oauth2 tokens", "billing api uses oauth2 tokens keycloak service") == 1.0
    assert o("completely different subject matter entirely", "billing api oauth2 keycloak") == 0.0
    assert o("and the of to is", "anything at all here") == 0.0, "too short after stopwords -> refuse"
