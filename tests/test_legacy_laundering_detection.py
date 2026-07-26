"""A tamper laundered under <=1.67 stayed invisible forever — even after upgrading.

The <=1.67.0 defect (fixed in 1.68.0): edit a stored text out of band, then call the PUBLIC `slash()`.
`_emit_write_receipt` recomputed the commit from the record's CURRENT state, so the appended receipt
committed to the FORGED text and `verify_writes()` went False -> True.

What was not appreciated until this audit is what that leaves behind. Nothing in the past is rewritten --
a new, well-formed receipt is appended -- so:

  * the chain stays internally consistent;
  * an externally witnessed anchor still re-derives its prefix intact (measured: "prefix intact" for the
    laundered store AND for ordinary growth -- the witnessed history was never touched);
  * and 1.68-1.72 checked pre-split receipts only against the LATEST one, which is precisely the forged
    one, so upgrading carried the invisibility forward.

Measured end to end with installed packages: a store laundered on 1.67.0 and opened on 1.72.0 reported
verify_writes=True, served 'Revenue is 900M', and produced an audit bundle that verified ok=True.

`legacy_strict=True` (1.73.0, on by default) checks pre-1.68 receipts against EVERY receipt. It fails
CLOSED. The cost is a false positive on a legitimate pre-1.68 slash()/restore(), which is
indistinguishable from the attack by construction -- so the message says so rather than accusing anyone.
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


def _legacy_receipt_store(tamper: bool):
    """Reproduce a pre-1.68 store shape: receipts whose commit has no `immutable_sha256`.

    Built by writing normally and then stripping the split fields, which is what a store written by
    <=1.67 actually looks like on disk. The `slash()` that follows re-commits to whatever the text says
    at that moment -- the laundering step when the text was edited first, an ordinary revocation when it
    was not.
    """
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("Revenue is 100M", mtype="semantic", source={"doc": "bigfour-auditor.com"})

    # Strip the split fields AND re-hash, or the chain fails on a hash mismatch instead of on the check
    # under test -- a fixture that reports the right answer for the wrong reason.
    prev = core._GENESIS
    for r in m._receipts:
        r["commit"].pop("immutable_sha256", None)
        r["commit"].pop("mtype", None)
        r["prev"] = prev
        r["hash"] = core._sha256_hex(core._canon(Inspeximus._chain_core(r, "write")))
        prev = r["hash"]
    m._save(force=True)

    if tamper:
        next(x for x in m.items if x["id"] == rid)["text"] = "Revenue is 900M"

    # The LAUNDERING step, reproduced in the shape <=1.67 leaves behind: a SECOND old-format receipt whose
    # commit is recomputed from the record's state at that moment. With the text edited first that commit
    # blesses the forgery; with it untouched this is an ordinary revocation. Both are appended, both are
    # well-formed, and on disk they are identical in structure -- which is the whole problem.
    #
    # It has to be constructed rather than produced by calling slash(), because today's slash() emits a
    # NEW-format receipt. A fixture with only ONE receipt does not reproduce the attack at all: that
    # receipt is also the latest, so even the old lenient rule catches the tamper, and the test would pass
    # without exercising anything. My first version did exactly that.
    rec = next(x for x in m.items if x["id"] == rid)
    commit = {"id": rec["id"],
              "content_sha256": core._sha256_hex(core._canon(
                  {"text": rec.get("text"), "key": rec.get("key"), "mtype": rec.get("mtype")})),
              "attrib_sha256": core._sha256_hex(core._canon(sorted(Inspeximus._rec_sources(rec))))}
    second = {"seq": len(m._receipts), "ts": rec.get("ts"), "memory_id": rid,
              "commit": commit, "prev": m._receipts[-1]["hash"]}
    second["hash"] = core._sha256_hex(core._canon(Inspeximus._chain_core(second, "write")))
    m._receipts.append(second)
    m._save(force=True)
    return m, rid


def test_a_legacy_laundered_store_is_now_flagged():
    """THE finding. Before 1.73.0 this returned True and there was no way to learn otherwise."""
    m, _rid = _legacy_receipt_store(tamper=True)
    ok, problems = m.verify_writes()
    assert ok is False, "a laundered pre-1.68 store must not report clean"
    assert any("PRE-1.68" in p for p in problems), problems


def test_the_message_does_not_accuse_and_says_what_to_do():
    """A legitimate pre-1.68 slash is indistinguishable from the attack, so the wording matters as much as
    the detection: an alarm that overstates gets switched off."""
    m, _rid = _legacy_receipt_store(tamper=True)
    note = next(p for p in m.verify_writes()[1] if "PRE-1.68" in p)
    assert "may be benign" in note, note
    assert "copy you trust" in note, note
    assert "legacy_strict=False" in note, "the opt-out must be named where the alarm is raised"


def test_the_opt_out_restores_the_previous_behaviour():
    m, _rid = _legacy_receipt_store(tamper=True)
    assert m.verify_writes(legacy_strict=False)[0] is True


def test_an_untouched_legacy_store_is_not_flagged():
    """The alarm must not fire on every pre-1.68 store, or it is noise and gets ignored."""
    m, _rid = _legacy_receipt_store(tamper=False)
    ok, problems = m.verify_writes()
    assert ok is True, problems


def test_a_modern_store_is_unaffected_by_the_flag():
    """Post-split receipts are checked by the stronger field-wise rule; legacy_strict must not touch them."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    for _ in range(20):
        m.credit([rid], outcome=True)
    assert next(r for r in m.items if r["id"] == rid)["mtype"] == "semantic"
    m.slash([rid], scope="memory")

    assert m.verify_writes()[0] is True, "a legitimate modern amendment must stay clean"
    assert not any("PRE-1.68" in p for p in m.verify_writes()[1])

    next(r for r in m.items if r["id"] == rid)["text"] = "FORGED"
    assert m.verify_writes()[0] is False


@pytest.mark.parametrize("strict", [True, False])
def test_the_flag_never_changes_a_clean_modern_store(strict):
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("an ordinary fact")
    m.remember("another ordinary fact")
    assert m.verify_writes(legacy_strict=strict)[0] is True
