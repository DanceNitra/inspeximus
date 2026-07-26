"""Round nine: my own 1.67.0 fix opened a laundering path through the public API.

1.67.0 fixed a false tamper alarm — `slash()` rewrites `mtype`, which the write receipt commits to, so a
legitimate revocation made `verify_writes()` report "edited after write". The fix let `slash()` AMEND the
chain and had verification bind only the LATEST receipt.

But the commit was ONE hash over text+key+mtype. "Only the latest binds" therefore forgave the text as well,
and `_emit_write_receipt` recomputes the commit from the record's CURRENT state. So: edit the text out of
band, call the public `slash()` — no key, no privilege, it is on the documented API — and the amendment
re-commits the forged text. `verify_writes()` went False -> True with "Revenue is 900M" standing.

For a tamper-evidence product that is the worst shape a defect can take: the accountability lever launders
the tamper — and it propagates into `erasure_certificate.self_check`, the document handed to a DPA.
(`audit_bundle` is NOT on this path: it is content-free by design and never bound the live text.) The fix
splits the commit — text+key are immutable in an append-only store, so
they bind on EVERY receipt forever; only `mtype` is forgiven on an amendment.

Third release in a row where the defect was inside the previous release's fix.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _tampered():
    """A store whose stored text was edited out of band, with the alarm already firing."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("Revenue is 100M", mtype="semantic", source={"doc": "bigfour-auditor.com"})
    next(r for r in m.items if r["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)
    assert m.verify_writes()[0] is False, "fixture must start with the alarm firing"
    return m, rid


def test_slash_cannot_launder_an_out_of_band_edit():
    """THE finding. `slash()` needs no key and no privilege — an attacker who reached the store file can
    also call it. It must not be able to clear the alarm."""
    m, rid = _tampered()
    m.slash([rid], scope="memory")
    ok, problems = m.verify_writes()
    assert ok is False, "a public API call cleared a tamper alarm"
    assert any("no longer matches its write receipt" in p for p in problems), problems
    assert next(r for r in m.items if r["id"] == rid)["text"] == "Revenue is 900M", \
        "and the forged text is still what the store serves — which is why the alarm must stay up"


def test_restore_cannot_launder_either():
    """The symmetric lever emits the same amendment."""
    m, rid = _tampered()
    m.slash([rid], scope="memory")
    m.restore([rid], scope="memory")
    assert m.verify_writes()[0] is False


def test_repeated_amendments_do_not_wear_the_alarm_down():
    """Bounded checks sometimes forget after N events. This one must not."""
    m, rid = _tampered()
    for _ in range(6):
        m.slash([rid], scope="memory")
        m.restore([rid], scope="memory")
    assert m.verify_writes()[0] is False


def test_the_laundering_does_not_reach_the_erasure_certificate():
    """`erasure_certificate` embeds `verify_writes` as its `self_check`, and that document is what gets
    handed to an auditor or a DPA. A laundered verdict would have been exported as evidence.

    (`audit_bundle` is deliberately CONTENT-FREE — it exports receipt hashes, not records — so it never
    bound the live text in the first place and is not on this path.)"""
    m, rid = _tampered()
    m.slash([rid], scope="memory")
    check = m.erasure_certificate()["self_check"]
    assert check["verified"] is False, check


# ── the 1.67.0 behaviour that had to survive the fix ─────────────────────────────────────────────────
def _graduated():
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    for _ in range(20):
        m.credit([rid], outcome=True)
    assert next(r for r in m.items if r["id"] == rid)["mtype"] == "semantic", "fixture must graduate"
    return m, rid


@pytest.mark.parametrize("ops", [("slash",), ("slash", "restore"), ("slash", "restore", "slash")])
def test_a_legitimate_amendment_is_still_forgiven(ops):
    """The false positive 1.67.0 removed must stay removed — it fired in 27 of 45 random sequences."""
    m, rid = _graduated()
    for op in ops:
        getattr(m, op)([rid], scope="memory")
    ok, problems = m.verify_writes()
    assert ok is True, problems


def test_a_genuine_edit_after_an_amendment_is_caught():
    """The order that hides a tamper behind a legitimate operation."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["text"] = "EDITED"
    assert m.verify_writes()[0] is False


def test_a_key_edit_is_caught_the_same_way():
    """`key` decides which value supersedes which, so it is immutable for the same reason `text` is."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["key"] = "payout::wallet"
    assert m.verify_writes()[0] is False


def test_an_mtype_edit_outside_slash_is_still_caught():
    """Forgiving `mtype` on the LATEST receipt must not mean nobody ever checks it. An out-of-band
    promotion to `semantic` buys durability and ranking, so it has to remain visible."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    assert m.verify_writes()[0] is True
    next(r for r in m.items if r["id"] == rid)["mtype"] = "procedural"
    assert m.verify_writes()[0] is False, "an unamended mtype change must not pass"


def test_the_commit_separates_immutable_fields_from_the_amendable_one():
    """Structural: if a later change folds the fields back into one hash, the laundering path reopens.
    This asserts the SHAPE, so that regression fails here rather than in a customer's audit."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("a fact", key="k")
    commit = m._write_commit(next(r for r in m.items if r["id"] == rid))
    assert "immutable_sha256" in commit and "mtype" in commit

    a = dict(commit)
    same_text_other_type = m._write_commit({"id": rid, "text": "a fact", "key": "k", "mtype": "procedural"})
    assert same_text_other_type["immutable_sha256"] == a["immutable_sha256"], \
        "mtype must not feed the immutable hash, or an amendment cannot forgive it alone"
    other_text = m._write_commit({"id": rid, "text": "another fact", "key": "k", "mtype": a["mtype"]})
    assert other_text["immutable_sha256"] != a["immutable_sha256"]


def test_a_pre_split_receipt_still_verifies():
    """Backward tolerance: a store written by <=1.67 has no `immutable_sha256`. It must keep verifying
    (under the weaker whole-commit rule), not start reporting tampering on every record."""
    m, rid = _graduated()
    prev = core._GENESIS
    for r in m._receipts:                                  # rebuild as a pre-1.68 chain, hashes and all
        r["commit"].pop("immutable_sha256", None)
        r["commit"].pop("mtype", None)
        r["prev"] = prev
        r["hash"] = core._sha256_hex(core._canon(
            {k: r[k] for k in ("seq", "ts", "memory_id", "commit", "prev")}))
        prev = r["hash"]
    ok, problems = m.verify_writes()
    assert ok is True, problems


# ── the same class, one field over: attribution ──────────────────────────────────────────────────────
def test_a_relabel_after_an_amendment_is_still_caught():
    """The FIRST fix bound text+key on every receipt but still forgave `attrib_sha256` on an amendment —
    and detecting a later RELABEL is the entire reason attribution is committed at all. Nothing in 541
    tests bound it: dropping the attribution check from `verify_writes` survived the whole suite.

    So the fix became structural. A receipt now DECLARES which fields it amends (`amends: ["mtype"]`),
    verification forgives exactly those and nothing else, and no call site declares attribution."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    assert m.verify_writes()[0] is True
    # attribution = own canonical source + inherited taint (`_rec_sources`); `derived_from` only feeds it
    # at write time, so mutating that alone is NOT a relabel — my first version of this test edited it and
    # passed under the broken code. Test the field the check actually reads.
    next(r for r in m.items if r["id"] == rid)["taint"] = ["bigfourauditor"]
    assert m.verify_writes()[0] is False, "a relabel must not ride in on a legitimate amendment"


def test_a_relabel_with_no_amendment_at_all_is_caught():
    """Baseline — the check the suite never had."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("a fact", source={"doc": "honest.example"})
    assert m.verify_writes()[0] is True
    next(r for r in m.items if r["id"] == rid)["source"] = {"doc": "bigfour-auditor.com"}
    assert m.verify_writes()[0] is False


def test_slash_declares_only_mtype():
    """The declaration is the authorisation, so its scope is the security property."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    m.restore([rid], scope="memory")
    amendments = [r.get("amends") for r in m._receipts if r.get("amends")]
    assert amendments == [["mtype"], ["mtype"]], amendments


def test_a_forged_amends_field_breaks_the_chain():
    """If `amends` were outside the receipt hash, an attacker would append
    `"amends": ["immutable_sha256"]` to an existing receipt and switch the text check off while the chain
    still verified. An unhashed authorisation is not an authorisation."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["text"] = "FORGED"
    assert m.verify_writes()[0] is False

    m._receipts[0]["amends"] = ["immutable_sha256"]        # try to authorise the edit after the fact
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("hash mismatch" in p for p in problems), problems


def test_an_appended_amends_cannot_silence_an_earlier_receipt():
    """The other direction: forge the declaration on the LATEST receipt instead."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["text"] = "FORGED"
    m._receipts[-1]["amends"] = ["mtype", "immutable_sha256"]
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("hash mismatch" in p for p in problems), problems
