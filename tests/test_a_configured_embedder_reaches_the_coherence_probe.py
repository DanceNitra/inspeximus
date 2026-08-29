"""The embedder precondition was asked of a copy whose embedder had just been removed.

`audit_the_audits` evaluates each probe's precondition against a COPY of the caller's store, and
`_open_copy` strips the embedder on purpose: the caller's may be a network call, and an audit that
fires one per record is a stall rather than an audit. The consequence was never followed through.
`_needs_embedder` asks whether the store it is handed has an embedder, so on that copy it answered
no every time, for every caller, however well configured. `index_coherence` could not reach the store
tier from any store that has ever existed.

A check that cannot see its target reports the same thing every time, and that thing looks like a
finding about your data. It was a finding about the harness.

The fix keeps the reason the embedder was stripped. The copy still never calls the caller's embedder:
it carries the deterministic stub, and inherits the caller's `embed_id` so the persisted vectors in
the copied file still match the recipe the store recorded. What is under test is whether a stale
index is noticed, never how well anything embeds.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _embed(text):
    """Deterministic and offline. A test that measures an embedder measures the wrong thing."""
    return [b / 255.0 for b in hashlib.sha256(text.encode("utf-8")).digest()[:16]]


def _store(**kw):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), **kw)
    for i in range(3):
        ix.remember("a fact numbered %d" % i, key="k%d" % i, object=str(i))
    ix.flush()
    return ix


def _row(out, surface):
    return next((p for p in out["probes"] if p.get("surface") == surface), None)


def test_a_store_with_an_embedder_reaches_the_coherence_probe_on_its_own_records():
    ix = _store(embed=_embed, persist_vectors=True, embed_id="test-embedder-v1", receipts=True)
    out = ix.audit_the_audits()
    row = _row(out, "index_coherence")
    assert row["outcome"] == "NOTICED", (
        "a configured embedder must let this surface be exercised on the caller's own index; got %r"
        % (row,))
    assert row["tier"] == "your store", row
    assert "index_coherence" in out["surfaces"]["demonstrated_on_your_store"]


def test_the_probe_starts_from_a_coherent_index():
    """CONTROL. If the clean copy were already incoherent, noticing the corrupt one proves nothing."""
    ix = _store(embed=_embed, persist_vectors=True, embed_id="test-embedder-v1", receipts=True)
    row = _row(ix.audit_the_audits(), "index_coherence")
    assert row.get("clean_before") is True, row
    assert row.get("clean_after") is False, row


def test_the_live_store_is_still_coherent_afterwards():
    """CONTROL. The probe must corrupt a copy, never the caller's index."""
    ix = _store(embed=_embed, persist_vectors=True, embed_id="test-embedder-v1", receipts=True)
    ix.audit_the_audits()
    assert ix.index_coherence()["coherent"] is True


# ───────────────────────────────────────────── the other direction
def test_a_store_with_no_embedder_still_reports_the_gap_rather_than_a_pass():
    """CONTROL, and the whole point of keeping the bucket. A lexical store has no index to fall
    behind, and saying so is the honest answer. Reporting it as demonstrated would be the inflation
    this method exists to prevent."""
    ix = _store(receipts=True)
    out = ix.audit_the_audits()
    row = _row(out, "index_coherence")
    assert row["outcome"] == "NOT_REACHABLE_HERE", row
    assert "index_coherence" not in out["surfaces"]["demonstrated_on_your_store"]
    assert "index_coherence" in out["surfaces"]["working_but_unreachable_here"]


def test_a_ram_only_index_is_not_counted_either():
    """CONTROL. persist_vectors=False means there is no persisted index in the file to fall behind."""
    ix = _store(embed=_embed, persist_vectors=False, embed_id="test-embedder-v1", receipts=True)
    out = ix.audit_the_audits()
    assert "index_coherence" not in out["surfaces"]["demonstrated_on_your_store"]
