"""A tenant view's PRIVATE helpers are scoped too, not only its public methods.

`_TenantView.__getattr__` has two halves, one line apart, and until 2026-08-15 they disagreed:

    if name.startswith("_") or name in _STORE_LEVEL:
        return getattr(self._parent, name)        # private: forwards EVERYTHING, fails OPEN
    ...
    raise AttributeError(f"{name}() is not classified for tenant views. ...")   # public: fails CLOSED

The public half was built after a measured incident recorded in its own docstring — "the previous
version forwarded EVERYTHING, and 54 of 79 public methods reached the parent as tenant=None (admin)".
The private half was never given the same treatment, so a correctly-rebound public method could still
read the whole store through an un-rebound helper it called. Four cross-tenant defects were confirmed
BY EXECUTION on 2026-08-15, all with that one root cause:

  * `route()` is rebound; `_route_key`/`_route_chain` were not. `acme.route("go back to the very first
    payout")` returned globex's value and wrote it into acme — and in reverse, a value acme planted on
    globex's key became globex's own current value on its next route().
  * `revert_challenge` is rebound; `_current_active_id` was not, so it handed out globex's record id.
  * `ratify`, `admit` and `classify_reversion` were classified `_STORE_LEVEL` and are not store-level:
    each reads record rows and returns or mutates record content.

The behavioural tests below pin those five. The structural test after them is the one that matters
in a year: it fails when a NEW private helper reads records without being rebound, which is how this
class of defect is born rather than how it is exploited.
"""
from __future__ import annotations

import inspect
import os
import re
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.core import _TenantView

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "inspeximus", "core.py")

# Private helpers that are store-wide by nature: a tenant does not have its own capacity, its own
# bytes on disk, or its own embedding matrix. Every entry is a decision, not an oversight.
_STORE_WIDE_PRIVATE = {
    "__init__", "_save", "_load_from_disk", "_atomic_write",
    "_vec_matrix", "_null_context",
    # `_evict_to_capacity` WAS here, declared store-wide on the reasoning that capacity is a property
    # of the file. That reasoning was wrong and the test did its job by forcing the decision into
    # writing where it could be read and refuted: on a shared store at capacity=10, one tenant writing
    # five of its own records hard-deleted five of another's, through forget(), silently. A helper
    # that DELETES across a security boundary is not store-wide however the cap is defined. Capacity
    # is now per-tenant on a bound handle -- a shared cap any tenant can spend on another's records is
    # a delete primitive, not a cap.
    # The two sidecar WRITERS persist the whole store-global dict on purpose; only the READERS
    # (_cusum_state / _budget_state) are per-tenant, and both are rebound.
    "_save_cusum", "_save_budget",
    # `_flush_tombstones` persists the WHOLE chain to its sidecar, the way _save_cusum does; the file
    # is one file and writing it is not a tenant act.
    "_flush_tombstones",
    # `_merkle_leaves` feeds anchor()/witness(), which are already declared store-level: an anchor is
    # a commitment over the whole log and a scoped one would not verify -- the same either/or as
    # erasure_certificate. Store-wide on purpose, not for want of scoping.
    "_merkle_leaves",
}


#: What makes a private helper TENANT-SENSITIVE, and therefore something that must run with a tenant.
#:
#: This started as `self.items|self._items` -- "reads records". Too narrow, twice, and each time the
#: gap was found by an attack rather than by the rule:
#:   * `_cusum_state` / `_budget_state` read a per-tenant SIDECAR, not records.
#:   * `_emit_tombstone` writes a tombstone, and reads `self.tenant` to stamp it.
#: So the detector was widened twice by adding whatever the last miss touched, which is a denylist by
#: omission -- exactly what let a seventh helper through.
#:
#: `self.tenant` is the general predicate: a private method that consults it is MAKING A TENANT
#: DECISION, and forwarded to the parent it will always read None and decide as admin. There is no
#: way to make that decision correctly while un-rebound. The collection patterns stay as a second
#: net for helpers that filter rows without naming `tenant` directly.
_TENANT_SENSITIVE = (r"self\.tenant\b|self\._tenant_rows\b"
                     r"|self\.items|self\._items|self\._cusum\b|self\._irrev\b|self\._tombstones\b")


def _toy(text: str):
    v = [0.0] * 16
    for w in text.lower().split():
        v[hash(w) % 16] += 1.0
    return v


@pytest.fixture
def pair():
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    acme, globex = s.for_tenant("acme"), s.for_tenant("globex")
    acme.remember("acme unrelated note", key="acme_thing", object="ok")
    gid = globex.remember("payout wallet is 0xTRUE", key="payout", object="0xTRUE")
    globex.remember("payout wallet is 0xEVIL", key="payout", object="0xEVIL")
    return s, acme, globex, (gid if isinstance(gid, str) else gid["id"])


# ------------------------------------------------------------------ the five confirmed defects
def test_route_cannot_see_or_write_another_tenants_value(pair):
    s, acme, globex, _ = pair
    out = acme.route("go back to the very first payout")
    assert out.get("target") != "0xTRUE", f"globex's value crossed: {out}"
    assert acme._route_chain("payout") == [], "the version chain is unscoped"
    assert not any(r.get("object") == "0xTRUE" for r in acme.items), \
        "globex's value was written into acme"


def test_a_planted_value_does_not_become_the_victims_current_value(pair):
    """The reverse direction, which the scan did not claim and execution found: acme plants a value
    on globex's key, then globex's own route() installs it."""
    s, acme, globex, _ = pair
    acme.remember("payout wallet is 0xATTACKER", key="payout", object="0xATTACKER")
    assert "0xATTACKER" not in globex._route_chain("payout"), \
        "another tenant's write is in globex's version chain"


def test_revert_challenge_does_not_disclose_another_tenants_record_id(pair):
    s, acme, globex, gid = pair
    ch = acme.revert_challenge("payout")
    assert gid not in ch, f"globex's record id leaked in the challenge: {ch}"


def test_classify_reversion_is_scoped(pair):
    s, acme, globex, _ = pair
    r = acme.classify_reversion("globex payout wallet", "payout", _toy)
    assert r.get("target") is None and r.get("current") is None, f"object values crossed: {r}"


def test_ratify_cannot_reach_another_tenants_record(pair):
    s, acme, globex, gid = pair
    before = globex.grade(gid).get("grade")
    assert acme.ratify(gid, "reproduction", "mallory").get("ok") is False
    assert globex.grade(gid).get("grade") == before, "an outsider changed the grade"


def test_admit_writes_into_the_callers_own_tenant(pair):
    """The half that is a silent write-LOSS rather than a leak: an admitted record used to land with
    tenant=None, so the writer was told `admitted: True` and could never see it again."""
    s, acme, _, _ = pair
    r = acme.admit("a genuinely new acme fact worth keeping and long enough")
    assert r.get("admitted") is True, r
    row = [x for x in s.items if x["id"] == r["id"]]
    assert row and row[0].get("tenant") == "acme", f"admitted row escaped the tenant: {row}"
    assert any(x["id"] == r["id"] for x in acme.items), "the writer cannot see its own memory"


def test_admit_does_not_name_another_tenants_record(pair):
    s, acme, _, gid = pair
    r = acme.admit("payout wallet is 0xTRUE")
    assert r.get("duplicate_of") != gid and r.get("id") != gid, f"globex's id leaked: {r}"


def test_one_tenants_write_does_not_evict_anothers_records():
    """THE ONE THAT DELETES, and the seventh instance of this class found in a day.

    It was not missed -- it was DECLARED store-wide in `_STORE_WIDE_PRIVATE` above, on the reasoning
    that capacity is a property of the file. Measured refutation: on a shared store at capacity=10,
    globex writing five of its OWN records hard-deleted five of acme's, through forget(), with a
    tombstone naming no cause, and acme was told nothing. The declaration was the defect; the test
    forcing it into writing is what made it refutable.
    """
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=10)
    acme, globex = s.for_tenant("acme"), s.for_tenant("globex")
    for i in range(10):
        acme.remember(f"acme record number {i} about its own business", key=f"a{i}")
    assert len(acme.items) == 10
    for i in range(5):
        globex.remember(f"globex record number {i} about other business", key=f"g{i}")
    assert len(acme.items) == 10, "another tenant's ordinary write deleted acme's records"
    assert len(globex.items) == 5


def test_capacity_still_bounds_a_single_tenant():
    """The must-not-vacuum control. Scoping the evictor must not disable it -- a cap that never
    evicts would pass the test above for the wrong reason."""
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=10)
    acme = s.for_tenant("acme")
    for i in range(18):
        acme.remember(f"acme record number {i} about its own business", key=f"a{i}")
    assert len(acme.items) <= 10, f"the cap stopped biting: {len(acme.items)} rows"


# --------------------------------------------------------------------------- the structural rule
def test_no_unrebound_private_helper_reads_records():
    """THE test. The five above pin the instances; this one closes the class.

    Any private helper that reads `self.items` / `self._items` must either be rebound on
    `_TenantView` (so it runs scoped) or be named in `_STORE_WIDE_PRIVATE` above (a deliberate
    decision, in writing). Sixteen were neither when this was written.
    """
    src = open(CORE, encoding="utf-8").read()
    view_src = src[src.index("class _TenantView"):]
    rebound = set(re.findall(r"def (_\w+)\(self, \*a", view_src))
    offenders = []
    for name, fn in inspect.getmembers(Inspeximus, predicate=inspect.isfunction):
        if not name.startswith("_") or name in _STORE_WIDE_PRIVATE or name in rebound:
            continue
        try:
            body = inspect.getsource(fn)
        except OSError:
            continue
        if re.search(_TENANT_SENSITIVE, body):
            offenders.append(name)
    assert offenders == [], (
        "these private helpers read records but are neither rebound on _TenantView nor declared "
        f"store-wide, so a tenant view runs them as tenant=None: {sorted(offenders)}")


def test_the_structural_rule_can_actually_fail():
    """The must-fail control. Without it, the test above passes just as happily on a build where
    `inspect.getsource` returns nothing or the regex stops matching."""
    src = open(CORE, encoding="utf-8").read()
    view_src = src[src.index("class _TenantView"):]
    rebound = set(re.findall(r"def (_\w+)\(self, \*a", view_src))
    assert {"_route_chain", "_current_active_id", "_budget_state"} <= rebound, \
        "the fix under test is not present, so the rule above is measuring nothing"
    assert len(rebound) > 8, f"the rebind scan found only {len(rebound)} names — the regex is stale"
    assert re.search(r"self\.items", inspect.getsource(Inspeximus._route_chain)), \
        "the record-reading detector no longer matches a known record reader"


def test_public_and_private_halves_agree_on_failing_closed():
    """The public half raises on an unclassified name. Pin that it still does — the two halves are
    supposed to be one policy, and this is the half that was already right."""
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    v = s.for_tenant("acme")
    # The probe name must NOT start with an underscore. The first version of this test called it
    # `_probe_unclassified_public`, which took the PRIVATE branch and was forwarded silently — the
    # test written to check the public half was routed through the private one. That is the same
    # confusion the fix above exists to remove, reproduced in its own test within the hour.
    setattr(Inspeximus, "probe_unclassified_public", lambda self: list(self.items))
    try:
        with pytest.raises(AttributeError, match="not classified"):
            v.probe_unclassified_public
    finally:
        delattr(Inspeximus, "probe_unclassified_public")
