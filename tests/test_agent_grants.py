"""Agent-to-agent read grants: scoped, revocable, recorded — and FAIL-CLOSED.

Multi-agent is the ordinary deployment shape, and the moment two agents share a store the questions are
which agent may read which memories, who granted it, and how it is taken back. A survey of the agent-memory
field turned up no comparable scoped/revocable sharing primitive; that is an observation about our search,
not a claim about anyone's product. inspeximus does it deterministically, zero-LLM, in the single-file core.

THE SHAPE THIS FILE GUARDS AGAINST is the one this repository keeps meeting: a check that never sees its
target reports SAFE. Applied here that would be a test asserting an ungranted agent recalls nothing — which
also passes on an empty store, a broken fixture, or a typo'd query. So every denial in this file is paired
with a POSITIVE CONTROL proving the record was there to be denied, and the main test is a SWEEP over every
public method rather than a curated list of read surfaces: a leak through `provenance` while `recall` is
clean is a complete failure of the feature, and only the sweep can see it.

The structure mirrors tests/test_tenant_isolation_structural.py, which exists for exactly this shape.
"""
import inspect
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import _ACL_PREFIX, _TenantView, _is_acl_record

SECRET = "sk-alice-777-DO-NOT-LEAK"


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _fixture(direct: bool = False):
    """One store, one owner (alice) with one secret record, one outsider (eve).

    Two ways to bind an agent, protected differently, so both are swept. `as_agent()` returns a view whose
    default-deny __getattr__ refuses an unclassified method. `Inspeximus(path=..., agent=...)` has no view
    at all, so there the ONLY protection is that `self.items` is scoped — a method reading `_items` directly
    leaks on that path and is invisible on the other.
    """
    s = Inspeximus(path=_path(), receipts=True)
    if direct:
        alice = Inspeximus(path=s.path, receipts=True, agent="alice")
        eve = Inspeximus(path=s.path, receipts=True, agent="eve")
        alice._items = eve._items = s._items          # one shared list, three handles (fixture artifact)
        alice._file_sig = eve._file_sig = s._file_sig = None      # see the tenant fixture for why
    else:
        alice, eve = s.as_agent("alice"), s.as_agent("eve")
    rid = alice.remember(f"ALICE_SECRET {SECRET} payout runbook", tags=["billing"],
                         key="payout::wallet", object="0xTRUE",
                         source={"doc": "alice-src"}, meta={"scope": "finance"})
    return s, alice, eve, rid


# ── THE MEASUREMENT: grant -> read, no grant -> no read, revoke -> no read ───────────────────────────
def test_grant_then_read_then_revoke_then_no_read():
    """The headline, with a positive control at every step so a denial cannot be an empty store."""
    s, alice, eve, rid = _fixture()
    bob = s.as_agent("bob")

    # POSITIVE CONTROL: the record exists and is readable by SOMEBODY. Without this line every assertion
    # below also passes on a store where the write silently failed.
    assert SECRET in str(s.recall("payout runbook")), "fixture broken: the operator cannot see the record"
    assert SECRET in str(alice.recall("payout runbook")), "the owner must read its own record"

    assert bob.recall("payout runbook") == [], "an ungranted agent read the record"
    assert s.can_read("bob", rid)["allowed"] is False

    alice.grant("bob", tag="billing")
    hits = bob.recall("payout runbook")
    assert SECRET in str(hits), "a granted agent could not read the record"
    assert s.can_read("bob", rid) == {"allowed": True, "reason": "granted by 'alice' on tag='billing'",
                                      "via": s.grants("bob")[0]["id"], "problems": []}

    alice.revoke("bob", tag="billing")
    assert bob.recall("payout runbook") == [], "a revoked agent still read the record"
    assert s.can_read("bob", rid)["allowed"] is False
    # ...and the control again, on the SAME store, so "revoked" is distinguishable from "destroyed".
    assert SECRET in str(alice.recall("payout runbook"))


# ── CONTROL 1 (mandatory): revocation must not delete, and must not touch anyone else's grant ────────
def test_revocation_deletes_nothing_and_does_not_disturb_another_agents_grant():
    """An implementation that DELETED the record on revoke would pass the headline assertion above while
    destroying data, and one that revoked by clearing the whole ACL would silently cut off every other
    agent. Both are asserted against here, which is the only reason the headline test means anything."""
    s, alice, eve, rid = _fixture()
    bob, carol = s.as_agent("bob"), s.as_agent("carol")

    alice.grant("bob", tag="billing")
    alice.grant("carol", tag="billing")           # an independent grant to a DIFFERENT agent
    assert SECRET in str(bob.recall("payout runbook")) and SECRET in str(carol.recall("payout runbook"))

    alice.revoke("bob", tag="billing")

    assert SECRET in str(alice.recall("payout runbook")), "revocation destroyed the OWNER's record"
    assert next(r for r in s._items if r["id"] == rid)["status"] == "active", "the record was retired"
    assert SECRET in str(s.recall("payout runbook")), "the operator lost the record on a revoke"
    assert SECRET in str(carol.recall("payout runbook")), "revoking bob cut off carol's independent grant"
    assert bob.recall("payout runbook") == []
    assert [g["agent"] for g in s.grants()] == ["carol"]


def test_two_granters_of_the_same_selector_are_independent():
    """Alice and the operator can both grant bob the same tag. Revoking one must not end the other — they
    are different keys precisely so that keyed supersession cannot adjudicate across them."""
    s, alice, eve, rid = _fixture()
    bob = s.as_agent("bob")
    alice.grant("bob", tag="billing")
    s.grant("bob", tag="billing")                  # operator-wide grant on the same selector
    assert len(s.grants("bob")) == 2

    alice.revoke("bob", tag="billing")
    assert SECRET in str(bob.recall("payout runbook")), "revoking one granter ended another's grant"
    assert [g["by"] for g in s.grants("bob")] == ["*"]

    s.revoke("bob", tag="billing")
    assert bob.recall("payout runbook") == []


# ── CONTROL 2: with no grants configured, nothing changes ────────────────────────────────────────────
def test_a_store_with_no_grants_behaves_exactly_as_before():
    """No silent tightening. An unbound handle is the operator view and must be untouched by the existence
    of an ACL — including its record fields, which must not sprout an owner stamp nobody asked for."""
    s = Inspeximus(path=_path(), receipts=True)
    ids = [s.remember(f"row {i} about deploys and billing", tags=["ops"], key=f"k{i}", object=str(i))
           for i in range(5)]
    before = json.dumps(s.recall("deploys billing", k=5), sort_keys=True, default=str)
    report_before = s.memory_report()

    assert all("owner_agent" not in r and "tenant" not in r for r in s._items), (
        "an unbound write grew a scoping field it never carried before")
    assert s.grants() == [] and s.grant_log() == []

    # Reopen from disk: the persisted shape must be readable by a handle that knows nothing about grants.
    s2 = Inspeximus(path=str(s.path), receipts=True)
    after = json.dumps(s2.recall("deploys billing", k=5), sort_keys=True, default=str)
    assert after == before, "recall changed on a store that has no grants"
    assert s2.memory_report()["active"] == report_before["active"] == 5
    assert len(ids) == 5


def test_issuing_a_grant_does_not_change_what_the_operator_reads():
    """The other half of "no silent tightening": adding an ACL must not narrow the operator's view, and the
    grant records themselves must never surface as memories — a grant is an act, not a fact to recall."""
    s, alice, eve, rid = _fixture()
    # reinforce=False on BOTH sides: an ordinary recall bumps the record's value, so comparing two plain
    # recalls measures the reinforcement, not the grants, and the test would "fail" with no ACL involved.
    # n_before FIRST: memory_report() runs a full recall per sampled record to estimate duplicates, and
    # those recalls reinforce. Taking the snapshot after it measured the report, not the grants.
    n_before = s.memory_report()["active"]
    before = json.dumps(s.recall("payout runbook", k=10, reinforce=False), sort_keys=True, default=str)

    s.grant("bob", scope="finance")
    s.grant("dave", key="payout::wallet")
    s.revoke("dave", key="payout::wallet")

    assert json.dumps(s.recall("payout runbook", k=10, reinforce=False), sort_keys=True, default=str) == before
    assert not any(_is_acl_record(r) for r in s._content_rows()), "an ACL act reached the content readers"
    for query in ("granted read access", "ACL", "bob", "revoked"):
        assert not any(_is_acl_record(h) for h in s.recall(query, k=20, reinforce=False)), (
            f"an access-control act was returned as a recall hit for {query!r}")
    assert s.contradictions() == [], "grant/revoke acts were flagged as contradictory memories"
    assert s.memory_report()["active"] == n_before, "grant bookkeeping was counted as memory"
    # ...but they ARE reachable from the audit surfaces, which is the whole point of storing them as records
    assert len(s.grant_log()) == 3 and len(s.history("acl::grant::*::dave::key::payout::wallet")) == 2


# ── THE SWEEP: every public method, not a curated list of read surfaces ──────────────────────────────
_ARGS = {
    # set_index_line writes onto a record chosen by key, so the sweep aims it at the OTHER
    # tenant's key: the leak it could produce is stamping an index line on a row that is not
    # ours, or naming that row's text back in the return value.
    "set_index_line": ("b/k", "a line aimed at another tenant's record"),
    # Same reason as the tenant sweep: these take a record id, so they are driven with an UNGRANTED
    # record's id rather than exempted. A method that refuses is a pass; one that echoes the content
    # of a record the caller may not read is the leak this test exists for.
    "confirm": (None, "swept"), "discard_provisional": (None, "swept"),
    # The RFC 6962 transparency surface: swept, not exempted. inclusion_proof returns a LEAF, so this
    # is the test that would catch it carrying another agent's content if the leaf ever grew a text field.
    "inclusion_proof": (0,), "merkle_consistency_proof": (0,), "merkle_root": (),
    "verify_inclusion": ({"leaf": "x", "index": 0, "tree_size": 1, "audit_path": [], "root": "00" * 32},),
    "history": ("payout::wallet",), "provenance": ("payout::wallet",), "as_of": ("payout::wallet", 9e9),
    "why_recalled": (f"ALICE_SECRET {SECRET}",), "revert": ("payout::wallet",),
    "verify_claim": (f"ALICE_SECRET {SECRET}",), "recall": (f"ALICE_SECRET {SECRET}",),
    "route": (f"ALICE_SECRET {SECRET}",), "check_conflict": (f"ALICE_SECRET {SECRET}",),
    "believed_at": ("payout::wallet", 9e9), "neighbors": (None,), "get": (None,), "grade": (None,),
    "convergence_report": (None,), "credit": ([None], True), "slash": ([None],),
    "monitor": ([None], False), "forget": ([None],), "restore": (None,),
    "spend_irreversible": ("alice-src", 1.0), "erasure_audit": ("alice-src",),
    "forget_subject": ("alice-src",), "forget_pii": (None, "alice-src"),
    "retract_lineage": ("alice-src",), "rederive": ("alice-src",),
    "remember": ("an eve row",), "remember_decision": ("d", "because", "ctx"), "admit": ("x",),
    "explain_growth": ({"n_writes": 0, "writes_tip": "", "n_tombstones": 0},),
    "apply_retention": (0.0,), "sleep": (), "reembed": (), "consolidate": (),
    "distill_and_remember": ("text", lambda t: []),
    "observe": (f"ALICE_SECRET {SECRET}", "payout::wallet"),
    "propagate_outcome": (True, [None]),
    "ratify": (f"ALICE_SECRET {SECRET}", "fact", "payout::wallet"),
    "recall_iterative": (f"ALICE_SECRET {SECRET}", lambda *a, **k: None),
    # the ACL surface itself, aimed at the owner's records
    "grant": ("mallory",), "revoke": ("mallory",), "grants": (), "grant_log": (),
    "can_read": ("eve", None), "as_agent": ("alice",),
    "graph": (), "subgraph": ("alice-src",),
    # A pure static scorer: it is HANDED a record rather than selecting one, so it has no rows to scope.
    # It is SWEPT rather than exempted anyway, and with the harshest input available -- a record carrying
    # the owner's secret verbatim -- so the sweep proves the scorer returns a number and does not echo the
    # content it scored. An exemption here would have asserted that by assumption instead of measuring it.
    "session_salience": ({"id": "alice-1", "text": f"ALICE_SECRET {SECRET}", "mtype": "decision",
                          "tags": ["decision"], "value": 1.0, "ts": 0.0},),
}

#: Methods the sweep cannot drive, each with a reason. Naming them deliberately is the point: the first
#: version of the tenant sweep skipped on TypeError, so a newly added leaking method was invisible to it.
_UNSWEEPABLE = {
    "for_tenant":       "returns another view; covered by the dedicated escalation test",
    "flush":            "persistence only, returns None",
    "register_erasure_target": "takes an adapter object, no records in the result",
    "verify_cosigned_anchor":  "static, operates on a passed-in anchor dict",
    "verify_consistency":      "compares two passed-in anchors",
    "check_self_narration":    "static text classifier, no store access",
    "classify_reversion":      "needs an embedder",
    "detect_split_view":       "compares two passed-in anchors",
    "submit_revert":    "needs a signed capability",
    "revert_capability": "mints a capability, needs a key",
    "revert_now":       "needs a signed capability",
    "revert_challenge": "needs a signed capability",
    "restore_now":      "needs a signed capability",
    "restore_intent":   "needs a signed capability",
    "revert_intent":    "needs a signed capability",
    "resolve_reopened": "needs a reopened id this agent can see",
    "promote_candidate": "needs a candidate id this agent can see",
    "discard_candidate": "needs a candidate id this agent can see",
    "support_challenge_for": "needs a challenge id this agent can see",
    "remember_dedup":   "a write path; covered by the write-side tests",
    "erasure_certificate": "signs over tombstones, no record text",
    "verify_erasure_certificate": "static, operates on a passed-in certificate",
    "verify_audit_bundle": "static, operates on a passed-in bundle",
    "verify_witness":   "static signature check",
    "verify_attribution": "operates on the receipt chain, content-free",
    "anchor":           "content-free chain head",
    "witness":          "takes no arguments; content-free chain head",
}
#: NOT exempt, deliberately: `graph` and `subgraph` are read surfaces that walk the record set, so they are
#: driven by the sweep (see _ARGS). The tenant sweep exempts them; copying that exemption here would have
#: left two graph-shaped readers untested by the only test that covers the whole surface -- an exemption
#: list is where methods go to avoid being tested, and this one would have been inherited rather than
#: decided. `subgraph` is aimed at the owner's own source string, which is the seed an outsider would use.


def _synthesise(name, sig, rid):
    """Best-effort arguments aimed AT THE OWNER'S RECORD, from the parameter names."""
    if name in _ARGS:
        return tuple(rid if x is None else ([rid] if x == [None] else x) for x in _ARGS[name])
    args = []
    for pname, p in list(sig.parameters.items())[1:]:            # skip self
        if p.default is not p.empty or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            break
        if pname in ("id", "memory_id", "rid"):
            args.append(rid)
        elif pname == "ids":
            args.append([rid])
        elif pname == "key":
            args.append("payout::wallet")
        elif pname in ("subject", "source", "doc"):
            args.append("alice-src")
        elif pname in ("query", "q", "text", "claim"):
            args.append(f"ALICE_SECRET {SECRET}")
        elif pname == "agent":
            args.append("eve")
        else:
            return None
    return tuple(args)


@pytest.mark.parametrize("direct", [False, True], ids=["as_agent view", "agent= bound store"])
def test_no_public_method_leaks_an_ungranted_records_content(direct):
    """THE structural test, and the only one that keeps working as the API grows.

    Every public method is called from an UNGRANTED agent's handle with arguments aimed at the owner's
    record, and the entire result is searched for the owner's secret. A method that raises or refuses is a
    pass. A method that cannot be driven must be named in `_UNSWEEPABLE` with a reason — skipping it
    silently is how a sweep stops being a sweep, and a leak through one surface is a total failure here.
    """
    leaked, exercised, undriveable = [], 0, []
    for name in sorted(dir(Inspeximus)):
        if name.startswith("_") or not callable(getattr(Inspeximus, name)) or name in _UNSWEEPABLE:
            continue
        s, alice, eve, rid = _fixture(direct=direct)
        try:
            sig = inspect.signature(getattr(Inspeximus, name))
        except (TypeError, ValueError):
            undriveable.append(name)
            continue
        args = _synthesise(name, sig, rid)
        if args is None:
            undriveable.append(name)
            continue
        try:
            out = getattr(eve, name)(*args)
        except TypeError as e:
            undriveable.append(f"{name} ({e})")
            continue
        except Exception:
            exercised += 1                                       # raised/refused is a pass
            continue
        exercised += 1
        if SECRET in str(out):
            leaked.append(name)

    assert not leaked, f"these public methods returned an ungranted record's content: {leaked}"
    assert not undriveable, (
        "the sweep could not drive these — add an entry to _ARGS so they ARE swept, or to _UNSWEEPABLE "
        f"with a reason: {undriveable}")
    assert exercised >= 40, f"the sweep only exercised {exercised} methods — it is not covering the surface"


def test_the_sweep_would_catch_a_leak():
    """The control for the test above: if the allow-list stopped filtering, the sweep must go red. Without
    this, "no method leaked" is indistinguishable from "the fixture never held a secret to leak"."""
    s, alice, eve, rid = _fixture()
    assert SECRET not in str(eve.recall(f"ALICE_SECRET {SECRET}"))
    s.grant("eve", tag="billing")                        # the ONLY change: the record becomes readable
    assert SECRET in str(eve.recall(f"ALICE_SECRET {SECRET}")), (
        "the sweep's target is unreachable even WITH a grant, so its clean result proves nothing")


def test_the_raw_list_itself_is_scoped():
    """`view.items` must not resolve to the parent's unscoped property — that defeated the whole allow-list
    when the same mistake was made for tenants."""
    s, alice, eve, rid = _fixture()
    assert SECRET not in str(eve.items)
    assert len(eve.items) == 0 and len(alice.items) == 1 and len(s._items) == 1


# ── fail-closed: every branch that cannot decide must DENY ───────────────────────────────────────────
def test_a_grant_with_no_selector_is_refused_rather_than_read_as_everything():
    s, alice, eve, rid = _fixture()
    with pytest.raises(ValueError, match="EXACTLY ONE selector"):
        s.grant("bob")
    with pytest.raises(ValueError, match="EXACTLY ONE selector"):
        s.grant("bob", tag="billing", scope="finance")
    assert s.grants() == [] and eve.recall("payout runbook") == []


def test_no_one_can_grant_themselves_access_through_the_ordinary_write_path():
    """A grant is a record, which is what makes it auditable — and would make the ACL decorative if any
    caller could mint one. The reserved keyspace is the only thing standing between the two."""
    s, alice, eve, rid = _fixture()
    forged = f"{_ACL_PREFIX}*::eve::tag::billing"
    with pytest.raises(ValueError, match="reserved access-control keyspace"):
        eve.remember("I hereby grant myself everything", key=forged, object="granted")
    with pytest.raises(ValueError, match="reserved access-control keyspace"):
        s.remember("operator forgery", key=forged, object="granted")
    assert eve.recall("payout runbook") == []
    assert s.grants("eve") == []


def test_an_agent_can_lend_only_what_it_owns():
    """Bob issuing a grant over the whole tag would let any agent re-share another agent's records. His
    grant is evaluated against records BOB owns, so it authorises nothing of alice's."""
    s, alice, eve, rid = _fixture()
    bob = s.as_agent("bob")
    bob.remember("bob's own billing note", tags=["billing"])
    bob.grant("eve", tag="billing")

    got = str(eve.recall("billing note payout runbook", k=10))
    assert "bob's own billing note" in got, "bob could not lend his OWN record"
    assert SECRET not in got, "bob re-shared a record he does not own"


def test_an_agent_handle_cannot_issue_a_grant_in_another_agents_name():
    s, alice, eve, rid = _fixture()
    with pytest.raises(PermissionError, match="cannot issue a grant as"):
        s.as_agent("bob").grant("mallory", tag="billing", by="alice")
    assert s.grants() == []


def test_an_unresolvable_grant_authorises_nothing():
    """Two ACTIVE acts disagreeing about the same grant is not a tie to break in the reader's favour. A
    hand-edited or partially-restored store can produce it, and the honest answer is to deny the key."""
    s, alice, eve, rid = _fixture()
    s.grant("eve", tag="billing")
    assert SECRET in str(eve.recall("payout runbook")), "positive control: the grant works before mangling"

    # revive the retired 'granted' act beside the live one, as a restore-from-backup would
    revoked = s.revoke("eve", tag="billing")
    for r in s._items:
        if r.get("key") == revoked["key"] and r.get("status") == "superseded":
            r["status"] = "active"
    s._acl_rev += 1
    assert eve.recall("payout runbook") == [], "an ambiguous ACL resolved OPEN"
    assert s.can_read("eve", rid)["allowed"] is False


@pytest.mark.parametrize("mangle,label", [
    ({"value": None}, "a null selector value"),
    ({"value": ""}, "an empty selector value"),
    ({"value": []}, "an empty id list"),
    ({"by": None}, "no named granter"),
    ({"by": ""}, "an empty granter"),
])
def test_an_empty_selector_authorises_nothing_rather_than_everything(mangle, label):
    """THE VACUITY TEST. `scope` and `key` are compared with `==` against a field most records do not carry,
    so a grant whose value went missing degenerates to `None == None` and authorises every record WITHOUT a
    scope -- the widest grant in the store, produced by the emptiest input, on the read path. An empty scope
    must deny, not match vacuously; in an access-control feature that difference is the whole feature.
    """
    for kind, sel in (("scope", {"scope": "finance"}), ("key", {"key": "payout::wallet"}),
                      ("tag", {"tag": "billing"}), ("ids", {"ids": ["x"]})):
        s, alice, eve, rid = _fixture()
        unscoped = s.remember("a plain operator row with no scope and no key")
        s.grant("eve", **sel)
        for r in s._items:
            if _is_acl_record(r):
                r["meta"]["acl"].update(mangle)
        s._acl_rev += 1

        seen = eve.recall("payout runbook plain operator row", k=10)
        assert seen == [], f"{kind} grant with {label} matched {[h['id'] for h in seen]}"
        assert s.can_read("eve", rid)["allowed"] is False, f"{kind} grant with {label} allowed the owner's row"
        assert s.can_read("eve", unscoped)["allowed"] is False, (
            f"{kind} grant with {label} vacuously matched the record that LACKS the field")


def test_no_grants_at_all_denies_rather_than_falls_through():
    """The empty-SET case, distinct from the empty-selector case above: zero grants must take the deny path,
    not a 'nothing to check, therefore fine' path. Measured against a store that definitely holds rows."""
    s, alice, eve, rid = _fixture()
    assert s.grants("eve") == [] and s.grant_log("eve") == []
    assert len(s._items) == 1, "positive control: there is a record here to be denied"
    assert eve.items == () and eve.recall("payout runbook") == []
    assert s.can_read("eve", rid) == {"allowed": False,
                                      "reason": "no active grant covers this record for this agent",
                                      "via": None, "problems": []}


def test_a_grant_that_matches_zero_records_grants_zero_records():
    """A grant naming a selector nothing carries is not an error and must not widen to a fallback."""
    s, alice, eve, rid = _fixture()
    s.grant("eve", tag="nonexistent-tag")
    assert len(s.grants("eve")) == 1, "positive control: the grant is in force"
    assert eve.recall("payout runbook") == []
    assert s.can_read("eve", rid)["allowed"] is False


def test_a_grant_with_an_unknown_selector_kind_denies():
    s, alice, eve, rid = _fixture()
    s.grant("eve", tag="billing")
    for r in s._items:
        if _is_acl_record(r):
            r["meta"]["acl"]["kind"] = "vibes"          # a kind no evaluator knows
    s._acl_rev += 1
    assert eve.recall("payout runbook") == []
    assert s.can_read("eve", rid)["allowed"] is False


def test_a_grant_never_reaches_across_a_tenant_boundary():
    s = Inspeximus(path=_path(), receipts=True)
    acme, globex = s.for_tenant("acme"), s.for_tenant("globex")
    acme.as_agent("alice").remember(f"ACME {SECRET}", tags=["billing"])
    globex.as_agent("gina").remember("globex billing note", tags=["billing"])

    globex.grant("bob", tag="billing")                   # granted inside GLOBEX only
    got = str(globex.as_agent("bob").recall("billing", k=10))
    assert "globex billing note" in got, "positive control: the in-tenant grant works"
    assert SECRET not in got, "a grant crossed a tenant boundary"
    assert acme.as_agent("bob").recall("billing") == []


def test_rescoping_a_view_cannot_shed_the_agent_binding():
    """`view.for_tenant(t)` returning an operator handle would be a privilege escalation reachable from
    inside the sandbox — the escape hatch is more dangerous than the missing feature."""
    s, alice, eve, rid = _fixture()
    assert eve.for_tenant("acme").agent == "eve"
    assert eve.as_agent("eve").agent == "eve"
    assert s.for_tenant("acme").agent is None            # the operator handle is unaffected


def test_a_bad_agent_id_is_refused_not_sanitised():
    """Quietly rewriting an identifier is how two different agents end up sharing one ACL entry."""
    s = Inspeximus(path=_path())
    for bad in ("", "   ", "a::b", "*", 7, None):
        with pytest.raises(ValueError):
            s.as_agent(bad)


# ── the audit trail: one chain, not a second log ─────────────────────────────────────────────────────
def test_every_grant_and_revocation_lands_in_the_existing_provenance_trail():
    s, alice, eve, rid = _fixture()
    g = alice.grant("bob", tag="billing", note="quarterly close")
    r = alice.revoke("bob", tag="billing")

    hist = s.history(g["key"])
    assert [h.get("object") for h in hist] == ["granted", "revoked"], hist
    prov = s.provenance(key=g["key"])
    assert prov["found"] and prov["current"]["object"] == "revoked", prov
    assert [t.get("object") for t in prov["timeline"]] == ["granted", "revoked"], prov["timeline"]
    assert {a["id"] for a in s.grant_log("bob")} == {g["id"], r["id"]}
    assert [a["state"] for a in s.grant_log("bob")] == ["revoked", "granted"]   # newest first
    assert s.grant_log("bob")[1]["note"] == "quarterly close"

    receipted = {rec["memory_id"] for rec in s._receipts}
    assert {g["id"], r["id"]} <= receipted, "an ACL act was not covered by the write-receipt chain"
    ok, problems = s.verify_writes()
    assert ok, problems


def test_the_audit_bundle_carries_the_acl_and_notices_a_forged_one():
    from inspeximus.audit_bundle import build_bundle, verify_bundle
    s, alice, eve, rid = _fixture()
    alice.grant("bob", tag="billing")
    alice.revoke("bob", tag="billing")
    alice.grant("carol", scope="finance")

    b = build_bundle(s)
    assert verify_bundle(b)["ok"], verify_bundle(b)["problems"]
    assert len(b["grants"]) == 3
    assert verify_bundle(b)["summary"]["grants_in_force"] == 1
    assert any("access control" in c for c in verify_bundle(b)["checks"])
    assert SECRET not in json.dumps(b), "memory text leaked into the bundle through the ACL block"

    # An operator who appends a grant that was never written must not verify clean.
    from inspeximus.core import _sha256_hex, _canon
    b["grants"].append({"id": "ffffffffff", "key": f"{_ACL_PREFIX}*::mallory::tag::billing",
                        "agent": "mallory", "by": "*", "kind": "tag", "value": "billing",
                        "state": "granted", "status": "active", "ts": 1.0})
    b["bundle_hash"] = _sha256_hex(_canon({k: v for k, v in b.items() if k != "bundle_hash"}))
    res = verify_bundle(b)
    assert not res["ok"] and any("access-control act" in p for p in res["problems"]), res


def test_grants_survive_a_reload():
    s, alice, eve, rid = _fixture()
    alice.grant("bob", tag="billing")
    s.flush()

    s2 = Inspeximus(path=str(s.path), receipts=True)
    assert SECRET in str(s2.as_agent("bob").recall("payout runbook")), "the grant did not survive a reload"
    assert s2.as_agent("mallory").recall("payout runbook") == []
    s2.revoke("bob", tag="billing", by="alice")
    assert s2.as_agent("bob").recall("payout runbook") == []


def test_every_selector_kind_selects_and_excludes():
    """A selector that matched everything would pass a grant test and fail as an ACL. Each kind is measured
    against a record it must admit AND one it must not."""
    s = Inspeximus(path=_path())
    a = s.as_agent("alice")
    hit = a.remember("the admitted row", tags=["billing"], key="payout::wallet",
                     object="0xTRUE", meta={"scope": "finance"})
    miss = a.remember("the excluded row", tags=["ops"], key="deploy::channel",
                      object="blue", meta={"scope": "infra"})
    for i, sel in enumerate([{"tag": "billing"}, {"scope": "finance"},
                             {"key": "payout::wallet"}, {"ids": [hit]}]):
        agent = f"reader{i}"
        s.grant(agent, **sel)
        seen = {h["id"] for h in s.as_agent(agent).recall("row", k=10)}
        assert seen == {hit}, f"selector {sel} admitted {seen}, expected only the target"
        assert s.can_read(agent, miss)["allowed"] is False, f"selector {sel} leaked the excluded row"


def test_an_agent_cannot_retire_a_record_it_cannot_read():
    """COMPOSITION WITH SUPERSESSION, measured rather than assumed — and the result was not what I expected.

    Supersession keys in this store are GLOBAL: from the operator handle, two writes sharing a `key` retire
    each other regardless of who wrote them (asserted below, because the grant layer must not quietly change
    it). Supersession is also UNAUTHENTICATED — core.py states that anyone who can call remember() and knows
    the key string retires the current value.

    Put those together in a multi-agent store and the write path is a hole the read ACL would not have
    closed: agent B guesses "payout::wallet" and agent A's current value is retired, without B ever being
    able to read it. So `_supersede_by_key` resolves against the handle's OWN scoped view, which means a
    write can only retire what its writer can see. Two agents keying the same string therefore each keep
    their own active record instead of clobbering one another.

    That is a deliberate consequence with a visible cost, and it is stated here rather than discovered
    later: from the OPERATOR view the store now holds two active records under one key, so an operator
    reading that key sees both values. Scoped writes buy write-isolation and pay for it in operator-side
    ambiguity; unscoped writes behave exactly as they always have.
    """
    s = Inspeximus(path=_path())
    # (1) the operator path is UNCHANGED: keys are global, last write wins
    s.remember("alpha retry limit is 5", key="config::retry", object="5")
    s.remember("alpha retry limit is 9", key="config::retry", object="9")
    active = [r for r in s._items if r.get("status") == "active" and r.get("key") == "config::retry"]
    assert len(active) == 1 and active[0]["object"] == "9", "the grant layer changed global supersession"

    # (2) the scoped path: bruno cannot see alice's record, so his write cannot retire it
    s2 = Inspeximus(path=_path())
    a, b = s2.as_agent("alice"), s2.as_agent("bruno")
    aid = a.remember("alice payout wallet is 0xTRUE", key="payout::wallet", object="0xTRUE")
    b.remember("bruno payout wallet is 0xEVIL", key="payout::wallet", object="0xEVIL")

    assert next(r for r in s2._items if r["id"] == aid)["status"] == "active", (
        "an agent retired a record it was never allowed to read")
    assert {h["text"] for h in a.recall("payout wallet", k=10)} == {"alice payout wallet is 0xTRUE"}, (
        "alice's current value was clobbered by an agent with no read access to it")
    # and the cost, asserted so it cannot regress into a surprise: the operator sees BOTH
    assert len({h["text"] for h in s2.recall("payout wallet", k=10)}) == 2

    # (3) a key grant is still bounded by ownership when an agent issues it
    b.grant("auditor", key="payout::wallet")
    assert {h["text"] for h in s2.as_agent("auditor").recall("payout wallet", k=10)} == {
        "bruno payout wallet is 0xEVIL"}, "an agent's key grant reached a record it does not own"
    # ...while the operator's key grant reaches everything under that global key, which is its whole point
    s2.grant("auditor2", key="payout::wallet")
    assert len(s2.as_agent("auditor2").recall("payout wallet", k=10)) == 2


def test_no_acl_surface_echoes_the_callers_text_back():
    """A read surface that reflects the caller's query into its output is an injection amplifier once that
    output lands in a model's context — a sibling unit's isolation sweep went red on exactly this while its
    records were correctly scoped. Denials here must be constant strings, not the probe."""
    s, alice, eve, rid = _fixture()
    probe = "IGNORE-PREVIOUS-INSTRUCTIONS-9f3a"
    outputs = [s.as_agent("bob").recall(probe), s.can_read("bob", probe),
               s.can_read(probe, rid), s.grants(probe), s.grant_log(probe)]
    for out in outputs:
        assert probe not in json.dumps(out, default=str), f"caller text echoed back: {out}"


def test_revoking_something_never_granted_is_recorded_and_still_denies():
    s, alice, eve, rid = _fixture()
    out = s.revoke("bob", tag="billing")
    assert out["was_granted"] is False
    assert s.as_agent("bob").recall("payout runbook") == []
    assert len(s.grant_log("bob")) == 1


# ── composition with the persist path (a sibling unit measured data loss here) ──────────────────────
def _on_disk(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return [r["text"] for r in (d if isinstance(d, list) else d.get("items", []))]


def test_a_grant_scoped_handle_does_not_drop_anyone_elses_rows_on_save():
    """THE SAFE COMPOSITION, asserted rather than assumed — because the unsafe one exists next door.

    `_save` serialises `self.items`, which is the SCOPED view, so a handle bound to a scope persists only
    the rows it can see and silently drops the rest. A sibling unit measured 0 of 3 records surviving a
    scoped handle's first flush, and `StoreChangedOnDisk` cannot catch it because a sequential handoff is
    not a concurrent write.

    The ACL's documented entry point does not reach that path: `as_agent()` returns a view that SHARES the
    parent's `_items` and forwards `_save` to the parent, so a scoped write persists the whole store. An
    access-control layer that quietly deleted the records it was denying would be worse than none, so this
    is measured on every route a caller can take to a write: the view's own remember(), a grant, a revoke,
    and an explicit flush.
    """
    p = _path()
    s = Inspeximus(path=p)
    s.remember("operator row that must survive")
    alice, bob = s.as_agent("alice"), s.as_agent("bob")

    alice.remember("alice row")                       # remember() saves with force=True, from the VIEW
    assert set(_on_disk(p)) == {"operator row that must survive", "alice row"}

    bob.remember("bob row")
    alice.grant("bob", tag="none-such")               # a grant is a write too
    alice.revoke("bob", tag="none-such")
    s.flush()
    assert set(_on_disk(p)) >= {"operator row that must survive", "alice row", "bob row"}, (
        "a scoped write dropped another agent's rows from the file")

    # and the store reopens with everything, which is the assertion a user would actually notice
    assert set(r["text"] for r in Inspeximus(path=p)._items) >= {
        "operator row that must survive", "alice row", "bob row"}


# THE DEFECT ITSELF IS NOT RE-ASSERTED HERE. A directly bound handle -- `Inspeximus(tenant=...)`, and
# equally `Inspeximus(agent=...)` -- still drops other rows on its first flush, and that is recorded, with
# the measurement and the line numbers, by
#     tests/test_session_continuity.py::test_p4_a_second_projects_save_cannot_destroy_the_firsts_records
# which landed with the session-continuity conformance work and owns the fix. Duplicating its xfail here
# would mean two tests flip on the day the persist path is fixed, and the second one arrives as a surprise
# to whoever fixed it. One record, in the file whose subject it is; this file asserts only the part that is
# about GRANTS -- that the ACL's own documented entry point does not reach that path (the test above).


def _records_from(out):
    """The RECORDS a multi-hop result carries, not its whole repr.

    Asserting on `str(result)` is unsafe here and that is a finding, not a nuisance: these surfaces echo
    the caller's own follow-up queries back in `followups_used`, so a query containing the probe string
    makes a substring check fire on text the caller supplied. That is not a leak -- it is the caller's own
    words -- but it is exactly how an over-broad assertion turns into a false positive, and an over-narrow
    one into a false negative. Read the records.
    """
    if isinstance(out, dict):
        rows = (out.get("hits") or []) + (out.get("new_hits") or []) + (out.get("merged") or [])
    else:
        rows = list(out or [])
    return " ".join(str(r.get("text", "")) if isinstance(r, dict) else str(r) for r in rows)


def test_the_multi_hop_surfaces_cannot_be_used_to_step_around_a_grant():
    """CROSS-FEATURE, and the reason it is asserted rather than assumed.

    `recall_iterative*` landed separately and forwards `**recall_kw` straight into `recall()`. A sibling
    unit shipped exactly that shape and it dropped a scope on the way through -- two independently-green
    changes producing a defect only once they were merged together. A multi-hop retriever that widened the
    ACL would be the same failure here, except that here it is an access-control bypass.

    It cannot happen by construction: the scoping lives on the STORE (`items`), not in a keyword a caller
    passes, so there is no argument to forget or forward wrongly. This test is what makes that claim
    falsifiable, and every leg carries a POSITIVE CONTROL -- the operator reaching the record through the
    SAME call -- because "the agent saw nothing" is also what a call that returns nothing to anyone says.
    Note `prior_ids=[]` on the follow-up leg: passing the round-1 ids there excludes the record as
    already-seen, and the denial would then be vacuous rather than enforced (measured while writing this).
    """
    s, alice, eve, rid = _fixture()
    q = "payout runbook"
    legs = {
        "recall_iterative_start": lambda h: h.recall_iterative_start(q, k=5),
        "recall_iterative": lambda h: h.recall_iterative(q, lambda *a, **k: [], k=5),
        "recall_iterative_followup": lambda h: h.recall_iterative_followup(q, [q], [], k=5),
    }
    for name, call in legs.items():
        assert SECRET in _records_from(call(s)),             f"positive control failed: the operator cannot reach the record via {name}"
        assert SECRET not in _records_from(call(eve)), f"{name} stepped around the ACL"

    s.grant("eve", tag="billing")                       # and the grant is what changes the answer
    for name, call in legs.items():
        assert SECRET in _records_from(call(eve)), f"{name} did not honour a real grant"


def test_the_project_scope_argument_cannot_widen_a_grant():
    """COMPOSITION with project scoping, which landed separately and added a `project=` argument to recall.

    Any new recall argument is a candidate bypass: if the ACL lived in a keyword, a caller passing a
    different one could step around it. It does not -- the allow-list is applied to `items` on the STORE,
    before any argument is read -- and that is what this pins. Each leg carries the operator reaching the
    record through the SAME call, so a denial cannot be a query that simply matched nothing.
    """
    s, alice, eve, rid = _fixture()
    for label, kw in (("default", {}), ("scope", {"scope": "finance"}), ("project", {"project": None})):
        assert SECRET in str(s.recall("payout runbook", k=5, **kw)), f"control failed for {label}"
        assert SECRET not in str(eve.recall("payout runbook", k=5, **kw)), f"{label} widened the ACL"

    # ...and a project-tagged write made through an agent handle stays that agent's until it is granted
    st = Inspeximus(path=_path())
    st.as_agent("alice").remember("PROJECTSECRET alpha release plan", tags=["plans"],
                                  meta={"project": "alpha"})
    eve2 = st.as_agent("eve")
    assert "PROJECTSECRET" in str(st.recall("release plan", k=5)), "control: the operator must see it"
    assert "PROJECTSECRET" not in str(eve2.recall("release plan", k=5))
    st.grant("eve", tag="plans")
    assert "PROJECTSECRET" in str(eve2.recall("release plan", k=5)), "the grant did not take effect"


# ── the surfaces ────────────────────────────────────────────────────────────────────────────────────
def test_the_acl_surface_is_classified_on_the_view():
    """Reaching grant()/can_read() through __getattr__ would run them on the PARENT, whose agent is None —
    so `alice.grant(...)` would be recorded as an operator grant over everything."""
    for name in ("grant", "revoke", "grants", "grant_log", "can_read", "as_agent"):
        assert name in _TenantView.__dict__, f"{name}() is not classified for scoped views"


def test_the_mcp_surface_grants_reads_and_revokes():
    """The MCP server is the surface most users actually touch; a wrapper that drops the selector is
    invisible to the library's own tests."""
    pytest.importorskip("mcp")
    import importlib
    os.environ["INSPEXIMUS_PATH"] = _path()
    os.environ["INSPEXIMUS_RECEIPTS"] = "1"
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))

    mem_id = mod._MEM.as_agent("alice").remember(f"MCP {SECRET} runbook", tags=["billing"])
    assert mod.recall_as("bob", "runbook") == []
    assert mod.can_read("bob", mem_id)["allowed"] is False

    assert mod.grant("bob", tag="billing", by="alice")["state"] == "granted"
    assert SECRET in str(mod.recall_as("bob", "runbook")), "the MCP grant did not take effect"
    assert SECRET in mod.get_as("bob", mem_id)["text"]
    assert mod.can_read("bob", mem_id)["allowed"] is True
    assert [g["agent"] for g in mod.grants()] == ["bob"]

    assert mod.revoke("bob", tag="billing", by="alice")["was_granted"] is True
    assert mod.recall_as("bob", "runbook") == []
    assert mod.get_as("bob", mem_id) == {}
    assert SECRET in str(mod.recall("runbook")), "the operator lost the record on a revoke"
    assert len(mod.grant_log()) == 2


def test_the_cli_grants_reads_and_revokes():
    import subprocess
    p = _path()
    env = dict(os.environ, INSPEXIMUS_NO_UPDATE_CHECK="1")

    def run(*args, expect=0):
        r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p, *args],
                           capture_output=True, text=True, env=env)
        assert r.returncode == expect, f"{args} -> rc={r.returncode}: {r.stderr}"
        return r.stdout

    run("remember", f"CLI {SECRET} runbook", "--tags", "billing")
    assert "nothing in memory" in run("recall", "runbook", "--as-agent", "bob")
    run("grant", "bob", "--tag", "billing")
    assert SECRET in run("recall", "runbook", "--as-agent", "bob")
    assert "bob" in run("grants")
    run("revoke", "bob", "--tag", "billing")
    assert "nothing in memory" in run("recall", "runbook", "--as-agent", "bob")
    assert SECRET in run("recall", "runbook"), "the operator lost the record on a revoke"
    assert run("grants").strip() in ("(no grants in this store)", "")
    assert "granted" in run("grants", "--log") and "revoked" in run("grants", "--log")
    # a refused grant must not exit 0 -- `inspeximus grant bob && echo shared` must not print `shared`
    run("grant", "bob", expect=2)


def test_a_denial_caused_by_a_broken_grant_is_distinguishable_from_no_grant_at_all():
    """Both read "bob sees nothing" from the outside, and only one is a store someone must go and fix. If
    can_read() could not tell them apart, the bounded problem log would be write-only -- recorded, never
    read, which is the same as not recorded."""
    s, alice, eve, rid = _fixture()
    assert s.can_read("eve", rid)["problems"] == [], "no grant at all is not a defect to report"

    s.grant("eve", tag="billing")
    for r in s._items:
        if _is_acl_record(r):
            r["meta"]["acl"]["value"] = None            # the vacuous selector, as a partial restore leaves it
    s._acl_rev += 1

    out = s.can_read("eve", rid)
    assert out["allowed"] is False
    assert out["problems"] and "authorises nothing" in out["problems"][0], out
