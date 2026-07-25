"""Round four, on 1.58.0 — three regressions from the previous round's fix, and three signed untruths.

Rounds one to three found defects. This round found that the concurrency guard shipped the round before had
the same shape as the bug it replaced: `None` meant both "no path" and "the file is not there yet", so two
handles bootstrapping a fresh store — the commonest concurrency case — were BOTH ungated. And the recovery
path added alongside it destroyed the property the store exists for.

The other half are worse in kind: `DeletionManifest.verify` and `ErasureAuditor.audit` both returned a
positive verdict on evidence that did not support it, and one of them signs its output.
"""
import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
from inspeximus.deletion_manifest import DeletionManifest, ErasureTarget
from inspeximus.erasure_auditor import ErasureAuditor


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── regressions from the 1.58.0 concurrency work ────────────────────────────────────────────────────
def test_two_handles_bootstrapping_a_fresh_store_do_not_clobber_each_other():
    """`_file_sig` was None both when the store had no path AND when the file did not exist yet, and `_save`
    skipped the guard on None — so the FIRST write from each handle was ungated. Two workers starting
    together on a new store is the commonest concurrency case there is."""
    p = _path()
    a, b = Inspeximus(path=p), Inspeximus(path=p)      # neither has seen a file
    a.remember("A-record")
    with pytest.raises(StoreChangedOnDisk):
        b.remember("B-record")
    assert [r["text"] for r in json.load(open(p, encoding="utf-8"))] == ["A-record"]


def test_reload_does_not_leave_two_active_records_under_one_key():
    """The recovery path took the DISK copy of a record this handle had superseded, so the merged store held
    two contradictory active values for one key — and `verify_writes()` returned True on it. The store's
    headline property, broken by the thing meant to repair it."""
    p = _path()
    a = Inspeximus(path=p, receipts=True)
    a.remember("salary is 100", key="pay")
    a.flush()
    b = Inspeximus(path=p, receipts=True)
    b.remember("city is Rome", key="city")
    b.flush()
    with pytest.raises(StoreChangedOnDisk):
        a.remember("salary is 200", key="pay")

    res = a.reload()
    assert res["demoted"] == 1
    rows = json.load(open(p, encoding="utf-8"))
    active_pay = [r["text"] for r in rows if r.get("key") == "pay" and r["status"] == "active"]
    assert active_pay == ["salary is 200"], rows


def test_reload_does_not_resurrect_a_record_this_handle_tombstoned():
    """Union-by-id brought a deliberately erased record back from disk."""
    p = _path()
    a = Inspeximus(path=p, receipts=True)
    a.remember("alice ssn 123", source={"doc": "alice"})
    a.flush()
    b = Inspeximus(path=p, receipts=True)
    b.remember("unrelated", source={"doc": "other"})
    b.flush()
    with pytest.raises(StoreChangedOnDisk):          # the erasure runs in memory, the save conflicts
        a.forget_subject("alice", request_id="DSAR-1", basis="gdpr-art17")

    a.reload()
    assert not any("alice ssn" in r["text"] for r in json.load(open(p, encoding="utf-8")))


def test_state_digest_is_stable_across_two_opens_of_identical_bytes():
    """Load-time normalisation used `time.time()` for a missing `ts`, so the digest changed on every open and
    a witness or anchor pinned to such a store could never re-verify."""
    p = _path()
    json.dump([{"id": "x1", "text": "legacy", "value": 1.0}], open(p, "w", encoding="utf-8"))
    first = Inspeximus(path=p).state_digest()
    time.sleep(0.02)
    assert Inspeximus(path=p).state_digest() == first


# ── verdicts that signed an untruth ─────────────────────────────────────────────────────────────────
class _Leaky(ErasureTarget):
    name = "crm"

    def erase(self, subject):
        return {"erased": 0}

    def still_recoverable(self, subject, values):
        return True


class _Clean(ErasureTarget):
    name = "crm"

    def erase(self, subject):
        return {"erased": 1}

    def still_recoverable(self, subject, values):
        return False


def test_a_deletion_manifests_verdict_must_follow_from_its_entries():
    """`complete`, `residual_targets`, `subject` and `authorized_by` sat OUTSIDE the hash chain, so flipping
    them produced a manifest that verified `(True, [])` — a signed lie on the one artifact whose entire job
    is to be evidence."""
    man = DeletionManifest()
    man.register(_Leaky())
    m = man.execute("alice", ["alice@example.com"], authorized_by="dpo@corp")
    assert man.verify(m) == (True, [])
    assert m["complete"] is False

    m["complete"] = True
    m["residual_targets"] = []
    ok, problems = man.verify(m)
    assert ok is False and any("does not follow from the evidence" in x for x in problems)


def test_an_empty_manifest_is_not_a_clean_one():
    assert DeletionManifest().verify({"entries": [], "complete": True})[0] is False


def test_a_genuinely_complete_manifest_still_verifies():
    man = DeletionManifest()
    man.register(_Clean())
    m = man.execute("alice", ["alice@example.com"])
    assert m["complete"] is True and man.verify(m) == (True, [])


def test_an_auditor_with_no_probes_does_not_report_erasure_verified():
    """It returned `erasure_verified: True` with `stores_audited: []` — and `compliance_receipt()` signs it.
    `DeletionManifest.execute` already guarded this exact case; `audit()` did not."""
    res = ErasureAuditor().audit("alice", ["alice@example.com"])
    assert res["erasure_verified"] is False
    assert res["stores_audited"] == []


# ── silent partial work, silent un-attribution, unbounded matching ──────────────────────────────────
def test_a_raising_predicate_aborts_the_erasure_instead_of_half_doing_it():
    """`forget(where=...)` skipped a record whose predicate raised and reported the success shape of a
    complete sweep: 2 forgotten, and the record the predicate choked on left behind."""
    m = Inspeximus(path=_path())
    for t in ("alpha secret", "beta secret", "gamma secret"):
        m.remember(t)

    def pred(r):
        if "beta" in r["text"]:
            raise RuntimeError("boom")
        return "secret" in r["text"]

    with pytest.raises(ValueError, match="partial match"):
        m.forget(where=pred)
    assert len(m.items) == 3, "a refused erasure must not have deleted anything"


def test_a_source_without_a_doc_key_is_refused_rather_than_silently_dropped():
    """It was accepted and then attributed to `id:<record id>`: provenance gone, `slash(scope='source')`
    matching nothing, and `verify_attribution` reporting ok on a relabel."""
    m = Inspeximus(path=_path())
    with pytest.raises(ValueError, match="'doc' key"):
        m.remember("x", source={"who": "trusted_manual", "url": "a"})

    rid = m.remember("x", source={"doc": "trusted_manual"})
    assert "trustedmanual" in Inspeximus._rec_sources(next(r for r in m.items if r["id"] == rid))


def test_route_does_not_match_a_key_inside_a_longer_word():
    """`route()` executes reverts and deletes on the match, and a default store has no revert authority
    configured — so "the earlier **heart** condition" reverted the key `art`, unconfirmed."""
    m = Inspeximus(path=_path())
    m.remember("art is a Monet", key="art", object="Monet")
    m.remember("art is a Renoir", key="art", object="Renoir")

    assert m.route("go back to the earlier heart condition").get("action") != "reverted"
    assert m.route("go back to the earlier art").get("action") == "reverted"


def test_the_cli_does_not_report_a_write_it_could_not_persist():
    """It printed `remembered <id>` and exited 0 on a store that never reached disk, so a typo'd --path
    silently discarded every write for the whole session."""
    import subprocess
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli",
                          "--path", os.path.join(tempfile.mkdtemp(), "nope", "deep", "m.json"),
                          "remember", "critical fact"], capture_output=True, text=True)
    assert out.returncode != 0
    assert "NOT PERSISTED" in out.stderr


def test_the_cli_still_succeeds_on_a_writable_store():
    import subprocess
    p = _path()
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p, "remember", "fine"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and os.path.exists(p)
