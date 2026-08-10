"""The critical defects found by the 2026-07-25 codebase audit, pinned so they cannot come back.

All three were found by auditing for a defect CLASS we had just fixed elsewhere — and all three had the same
shape as the thing that prompted the audit: an instrument reported safe while the guarantee was broken.
`recall()` honoured tenant isolation while `history()` handed over the other tenant's plaintext;
`verify_writes()` returned True while four of five records had never reached disk; and the collision guard
shipped in 1.53.0 covered one of five subject-scoped destructive paths.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus
from inspeximus.core import _TenantView


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── tenant isolation ────────────────────────────────────────────────────────────────────────────────
def _two_tenants():
    s = Inspeximus(path=_path())
    a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme note", key="acme thing", object="x")
    b.remember("GLOBEX_SECRET the globex api key is sk-globex-999",
               key="globex api key", object="sk-globex-999")
    return s, a, b


@pytest.mark.parametrize("call", [
    lambda a: [x.get("text", "") for x in a.history("globex api key")],
    lambda a: [str(a.provenance("globex api key").get("current"))],
    lambda a: [str(a.as_of("globex api key", 9e9))],
    lambda a: [str(x) for x in (a.why_recalled("globex api key", k=10) or [])],
])
def test_no_read_path_returns_another_tenants_text(call):
    """recall() was scoped; these four read the shared list directly and returned the secret verbatim."""
    _, a, _ = _two_tenants()
    assert "sk-globex-999" not in " ".join(call(a))


def test_a_tenant_cannot_delete_another_tenants_record():
    """`beta.forget([acme_id])` returned {'forgotten': 1} and the row was gone."""
    s, a, b = _two_tenants()
    acme_id = next(r["id"] for r in s.items if r.get("tenant") == "acme")
    res = b.forget([acme_id])
    assert res["forgotten"] == 0
    assert any(r["id"] == acme_id for r in s.items), "another tenant's record was hard-deleted"


def test_a_tenant_cannot_write_to_another_tenants_record():
    s, a, b = _two_tenants()
    acme_id = next(r["id"] for r in s.items if r.get("tenant") == "acme")
    assert b.credit([acme_id], outcome=True)["updated"] == []


def test_aggregate_reports_do_not_count_another_tenant():
    s, a, b = _two_tenants()
    assert a.memory_report()["total"] == 1
    assert a.state_digest() != b.state_digest(), "identical digests reveal the other tenant's state"


def test_an_unclassified_method_RAISES_rather_than_running_as_admin():
    """THE structural fix. The view forwarded everything it did not rebind, so 54 of 79 public methods ran
    against the shared store as tenant=None. A method added tomorrow must fail loudly, not leak quietly."""
    s = Inspeximus(path=_path())
    view = s.for_tenant("acme")

    def brand_new_method(self):                       # simulate tomorrow's addition
        return [r["text"] for r in self.items]
    Inspeximus.brand_new_method = brand_new_method
    try:
        with pytest.raises(AttributeError, match="not classified for tenant views"):
            view.brand_new_method()
    finally:
        del Inspeximus.brand_new_method


def test_every_public_method_is_classified():
    """Fails the moment someone adds a public method without deciding whether it is tenant-scoped."""
    public = {n for n in dir(Inspeximus)
              if not n.startswith("_") and callable(getattr(Inspeximus, n))}
    unclassified = sorted(public - set(_TenantView.__dict__) - _TenantView._STORE_LEVEL)
    assert not unclassified, f"classify these on _TenantView: {unclassified}"


# ── persistence ─────────────────────────────────────────────────────────────────────────────────────
def test_a_failed_save_is_not_reported_as_a_successful_one():
    """Measured pre-fix: 5 records in memory, 1 on disk, verify_writes() -> True."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    m.remember("fact 0")

    class Unserialisable:
        pass
    with pytest.raises(ValueError, match="JSON-serialisable"):
        m.remember("poison", meta={"o": Unserialisable()})   # rejected at the write that carries it

    for i in range(1, 4):
        m.remember(f"good fact {i}")
    m.flush()
    assert len(Inspeximus(path=p).items) == 4, "the poisoned write must not cost the good ones"


def test_verify_writes_reports_a_store_that_never_reached_disk():
    # A missing parent no longer fails: 1.64.0 creates it, because not creating it silently lost every
    # write on the install path the docs advertise. Use a parent that CANNOT become a directory — a file.
    d = tempfile.mkdtemp()
    blocker = os.path.join(d, "not-a-dir")
    open(blocker, "w", encoding="utf-8").write("i am a file")
    p = os.path.join(blocker, "m.json")
    m = Inspeximus(path=p, receipts=True)
    m.remember("fact 0")
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("not persisted" in x for x in problems)
    with pytest.raises(OSError):
        m.flush()


# ── the collision guard, on every destructive path ──────────────────────────────────────────────────
ALICE, BOB = "crm.example.com/alice", "crm.example.com/bob"


def _colliding_store():
    m = Inspeximus(path=_path(), receipts=True, pii_detect=True)
    m.remember("alice a@corp.com: the db host is old.host",
               key="db::host", object="old.host", source={"doc": ALICE})
    m.remember("bob b@corp.com notes: the db host is old.host", source={"doc": BOB})
    return m


@pytest.mark.parametrize("call", [
    lambda m: m.forget_subject(ALICE, request_id="r", basis="b"),
    lambda m: m.forget_pii(subject=ALICE, request_id="r"),
    lambda m: m.retract_lineage(ALICE),
    lambda m: m.rederive(ALICE),
])
def test_every_subject_scoped_destructive_path_spares_the_other_subject(call):
    """1.53.0 guarded forget_subject only. Its four siblings kept the defect: forget_pii hard-deleted Bob,
    retract_lineage demoted him, and rederive REWROTE his text and re-emitted it.

    ASSERTS THE OUTCOME, NOT THE MECHANISM. This required AmbiguousSubject, because ALICE and BOB were one
    canonical key and refusing was the only way to keep Bob's record intact. Subject matching is now
    path-preserving, so they are separate keys and each call operates on exactly its own subject -- Alice's
    request completes AND Bob is untouched, instead of Alice's request being refused. Bob surviving is what
    these four paths owe us; the exception was one way of paying it, and the weaker one, since it also made
    Alice's legal request unperformable.
    """
    m = _colliding_store()
    call(m)
    bob = [r for r in m.items if "bob" in (r.get("text") or "").lower()]
    assert len(bob) == 1, "the other subject's record was deleted by a request naming Alice"
    assert bob[0].get("status") == "active", f"the other subject's record was demoted: {bob[0].get('status')}"
    assert "b@corp.com" in bob[0]["text"], "the other subject's text was rewritten by another's request"


@pytest.mark.parametrize("call", [
    lambda m: m.forget_subject("crm.example.com/ghost", request_id="r", basis="b"),
    lambda m: m.forget_pii(subject="crm.example.com/ghost", request_id="r"),
    lambda m: m.retract_lineage("crm.example.com/ghost"),
    lambda m: m.rederive("crm.example.com/ghost"),
])
def test_a_subject_that_is_not_in_the_store_touches_nobody(call):
    """The ghost case, on all four paths.

    MEASURED before the fix: forget_subject('crm/nobody-here') -- a right-to-erasure request naming a
    person who was never written to this store -- hard-deleted BOTH of crm/alice's records and returned
    erased=2. The coarse canonical form keeps only the host, so every subject under it was one key and the
    ambiguity guard could not fire, because with a single real source in the bucket there is no collision
    to detect. Not a clean verdict about unexamined input: a DELETION on unexamined identity.
    """
    m = _colliding_store()
    before = [(r["id"], r.get("status"), r.get("text")) for r in m.items]
    call(m)
    after = [(r["id"], r.get("status"), r.get("text")) for r in m.items]
    assert after == before, "a request for a subject that is not in this store changed somebody else's data"


@pytest.mark.parametrize("call", [
    lambda m: m.forget_subject("User 42", request_id="r", basis="b")["erased"],
    lambda m: m.forget_pii(subject="User 42", request_id="r")["erased"],
    lambda m: m.retract_lineage("User 42")["demoted"],
])
def test_the_guard_does_not_break_intended_canonical_resolution(call):
    m = Inspeximus(path=_path(), receipts=True, pii_detect=True)
    m.remember("a note a@corp.com", source={"doc": "user-42"})
    assert call(m) == 1


def test_subject_resolution_lives_in_one_place():
    """The guard regressed because five call sites each inlined `cand = {subject, _canon_source(subject)}`.
    Fixing one fixed one. New subject-scoped paths must go through the shared resolver."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    inlined = len(re.findall(r"cand = \{subject, Inspeximus\._canon_source\(subject\)\}", src))
    assert inlined <= 3, (
        f"{inlined} inlined subject resolutions — use self._resolve_subject(), which carries the "
        f"collision guard. Allowed: _resolve_subject itself, forget_subject, and the read-only "
        f"erasure_audit.")


# ── 2.4.1: the meta dict was a second route into the library's own state ─────────────────────────
# `remember(meta=...)` copies the caller's dict onto the record verbatim, and the library reads its
# OWN decisions back out of that same dict. Measured on the published 2.4.0: mtype="semantic" alone
# correctly yields `unwarranted`, but adding meta={"graduated_from_episodic": True} yields `earned` --
# the top tier we report, on a record with no credit, no links and no witnesses. 2.4.0 had closed the
# mtype route and the hole simply moved one level down, which is why the fix is a keyspace.

def _first(store, mark):
    hits = store.recall(mark, k=10, with_warrant=True) or []
    assert hits, "INSTRUMENT DEAD: recall returned nothing, so a tier assertion would be vacuous"
    return hits[0]


def test_the_graduation_marker_is_not_writer_settable(tmp_path):
    """THE FIX. The top tier must not be reachable by asking for it."""
    s = Inspeximus(path=str(tmp_path / "g.json"), embed=None)
    s.remember("quorum-lattice claimed graduation", mtype="semantic",
               meta={"graduated_from_episodic": True})
    assert _first(s, "quorum-lattice").get("warrant") != "earned", (
        "a caller-supplied graduation marker reached the top trust tier")


def test_an_ordinary_meta_key_still_survives(tmp_path):
    """THE CONTROL. Reserving the library's keyspace must not swallow the caller's own metadata --
    without this, stripping everything would pass the test above and destroy the feature."""
    s = Inspeximus(path=str(tmp_path / "o.json"), embed=None)
    rid = s.remember("an ordinary record", meta={"ticket": "OPS-412", "confidence": 0.9})
    rec = next(r for r in s._items if r.get("id") == rid)
    assert (rec.get("meta") or {}).get("ticket") == "OPS-412"
    assert (rec.get("meta") or {}).get("confidence") == 0.9


@pytest.mark.parametrize("key,value", [("slashed", False), ("echo_blocked", False),
                                       ("objectless_blocked", False), ("needs_rederivation", False),
                                       ("superseded_by_toggle", True), ("revert_nonce", "forged"),
                                       ("acl", {"agent": "attacker", "state": "granted"})])
def test_no_library_decision_key_arrives_from_the_caller(tmp_path, key, value):
    """The CLASS, not the instance: every reserved key, one case each, so a new caller-settable
    decision key cannot be added without a red test."""
    s = Inspeximus(path=str(tmp_path / ("r_%s.json" % key)), embed=None)
    rid = s.remember("a record carrying %s" % key, meta={key: value})
    rec = next(r for r in s._items if r.get("id") == rid)
    assert key not in (rec.get("meta") or {}), "%s arrived from the caller" % key


def test_an_aliased_key_is_routed_not_dropped(tmp_path):
    """Aliases keep working: dropping them would break a caller who is getting what they intended."""
    s = Inspeximus(path=str(tmp_path / "a.json"), embed=None)
    rid = s.remember("aliased", meta={"aid": "analyst-7", "project": "orion"})
    rec = next(r for r in s._items if r.get("id") == rid)
    assert (rec.get("meta") or {}).get("aid") == "analyst-7"
    assert (rec.get("meta") or {}).get("project") == "orion"


def test_the_explicit_parameter_wins_over_the_meta_alias(tmp_path):
    """Two routes into one field need a stated precedence, or the answer depends on dict order."""
    s = Inspeximus(path=str(tmp_path / "p.json"), embed=None)
    rid = s.remember("conflict", meta={"aid": "from-meta"}, agent_id="from-parameter")
    rec = next(r for r in s._items if r.get("id") == rid)
    assert (rec.get("meta") or {}).get("aid") == "from-parameter"


def test_every_meta_key_the_library_reads_is_reserved():
    """THE DRIFT GUARD. A decision key added tomorrow and read out of meta would re-open this hole
    silently. Scan the source: anything the library reads back out of a record's meta must be either
    reserved or explicitly aliased."""
    import pathlib
    import re as _re
    from inspeximus.core import _RESERVED_META, _META_ALIASED_PARAM, _CALLER_META
    src = pathlib.Path(inspeximus_core_file()).read_text(encoding="utf-8")
    read = set(_re.findall(r'\.get\("meta"(?:, \{\})?\)\s*or\s*\{\}\)\.get\("([a-z_]+)"', src))
    read |= set(_re.findall(r'\.get\("meta", \{\}\)\.get\("([a-z_]+)"', src))
    # _CALLER_META is the DECLARED exception: read by us, written by the caller. Anything else that
    # the library reads must be closed to callers, or a writer can forge what we think we stamped.
    unguarded = sorted(read - set(_RESERVED_META) - set(_META_ALIASED_PARAM) - set(_CALLER_META))
    assert read, "INSTRUMENT DEAD: the scan found no meta reads at all, so it guards nothing"
    assert not unguarded, (
        "these meta keys are read by the library but a caller can still set them: %s" % unguarded)


def inspeximus_core_file():
    import inspeximus.core as _c
    return _c.__file__


def test_every_internal_write_of_a_reserved_key_uses_the_privileged_path():
    """THE GUARD THAT WOULD HAVE SAVED A 23-MINUTE RUN.

    `remember()` strips the reserved meta keyspace, and the library writes those same keys through
    it, so an internal call that forgets `_stamp()` silently loses its own marker. Enumerated by hand
    this was wrong three times in one change -- the third time cost 25 failing grant tests, because
    `_acl_write` builds its meta into a LOCAL VARIABLE and every hand-written scan looked for an
    inline `meta={`. So the enumeration is a test now.
    """
    import inspeximus.core as _c
    import pathlib
    import re as _re
    src = pathlib.Path(_c.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()
    offenders = []
    for m in _re.finditer(r"self\.(remember|remember_dedup|_stamp)\(", src):
        ln = src[:m.start()].count("\n")
        depth, k = 1, m.end()
        while depth and k < len(src):
            depth += (src[k] == "(") - (src[k] == ")")
            k += 1
        keys = set(_re.findall(r'"([a-z_]+)":', src[m.start():k]))
        above = "\n".join(lines[max(0, ln - 14):ln])
        for blk in _re.findall(r"meta\s*=\s*\{(.*?)\}", above, _re.S):
            keys |= set(_re.findall(r'"([a-z_]+)":', blk))
        if (keys & set(_c._RESERVED_META)) and m.group(1) != "_stamp":
            offenders.append("line %d: %s passes %s"
                             % (ln + 1, m.group(1), sorted(keys & set(_c._RESERVED_META))))
    assert not offenders, (
        "these internal writes pass a reserved meta key through the caller-facing path, so the "
        "library strips its own marker: %s" % offenders)


# ── causal staleness: did the SOURCE change, not how old is the record ───────────────────────────
# Our decay is temporal (a half-life on age) and age cannot tell a fact true for five years from one
# that rotted in a week. The causal question needs a source you can re-fetch -- measured 2026-08-10
# on our own deployment, 0.01% of 210,544 records carried one. check_sources() is that half.

def test_an_unchanged_source_is_fresh_and_a_changed_one_drifts(tmp_path):
    """THE FEATURE, with its own control: the same store must report FRESH before and DRIFTED after,
    or the verdict is about something other than the source."""
    doc = tmp_path / "runbook.md"
    doc.write_text("the release cadence is fortnightly", encoding="utf-8")
    s = Inspeximus(path=str(tmp_path / "s.json"), embed=None)
    s.remember("cadence is fortnightly", source={"doc": str(doc)})

    before = s.check_sources()
    assert before["counts"]["FRESH"] == 1 and before["ok"], before      # CONTROL
    doc.write_text("the release cadence is weekly", encoding="utf-8")
    after = s.check_sources()
    assert after["counts"]["DRIFTED"] == 1 and not after["ok"], after


def test_a_deleted_source_is_orphaned_not_fresh(tmp_path):
    """The half that never self-heals: a source that is gone emits one event and no later one."""
    doc = tmp_path / "policy.md"
    doc.write_text("old policy", encoding="utf-8")
    s = Inspeximus(path=str(tmp_path / "o.json"), embed=None)
    s.remember("the policy says X", source={"doc": str(doc)})
    assert s.check_sources()["counts"]["FRESH"] == 1                    # CONTROL
    doc.unlink()
    rep = s.check_sources()
    assert rep["counts"]["ORPHANED"] == 1 and not rep["ok"], rep


def test_a_store_with_no_fetchable_sources_reports_that_it_checked_NOTHING(tmp_path):
    """THE HONEST DENOMINATOR. `agent:scholar` is a writer, not a document -- exactly what 98.3% of
    our own records carry. Zero drifted over zero checked must not read like a clean store."""
    s = Inspeximus(path=str(tmp_path / "n.json"), embed=None)
    s.remember("a fact", source={"doc": "agent:scholar"})
    rep = s.check_sources()
    assert rep["counts"]["UNCHECKABLE"] == 1
    assert rep["checked"] == 0 and rep["checkable_fraction"] == 0.0
    assert not rep["ok"], "a store that verified nothing reported ok"
    assert "verified NOTHING" in rep.get("problem", "")


def test_the_writer_cannot_forge_the_source_fingerprint(tmp_path):
    """The fingerprint lives in the reserved keyspace for the same reason the trust tier does: a
    digest the writer can set is a drift check the writer can defeat."""
    doc = tmp_path / "spec.md"
    doc.write_text("v1", encoding="utf-8")
    s = Inspeximus(path=str(tmp_path / "f.json"), embed=None)
    s.remember("claims about the spec", source={"doc": str(doc)})
    doc.write_text("v2 — changed underneath", encoding="utf-8")
    rec = next(r for r in s._items if (r.get("meta") or {}).get("source_sha256"))
    forged = __import__("hashlib").sha256(doc.read_bytes()).hexdigest()
    s.remember("a second record trying to declare itself fresh",
               source={"doc": str(doc)}, meta={"source_sha256": forged, "source_seen_at": 0})
    rec2 = [r for r in s._items if r.get("text", "").startswith("a second record")][0]
    assert (rec2.get("meta") or {}).get("source_seen_at") != 0, "a caller set source_seen_at"
    assert s.check_sources()["counts"]["DRIFTED"] == 1, "the first record must still read DRIFTED"


def test_a_tenants_source_report_never_counts_another_tenants_records(tmp_path):
    """Scoped, unlike verify_attestations: the report carries record ids."""
    doc = tmp_path / "shared.md"
    doc.write_text("v1", encoding="utf-8")
    s = Inspeximus(path=str(tmp_path / "t.json"), embed=None)
    s.for_tenant("acme").remember("acme note", source={"doc": str(doc)})
    s.for_tenant("beta").remember("beta note", source={"doc": str(doc)})
    assert s.for_tenant("acme").check_sources()["total"] == 1
    assert s.check_sources()["total"] == 2          # the unbound view still sees everything
