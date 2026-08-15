"""`sleep()` ran a second copy of the supersession resolution that had none of the three guards.

THE SHAPE. `consolidate()` protects its state-toggle path three ways: a corroboration guard, a
persistence guard, and a fail-loud `reopened` flag on a refused overturn. `consolidate_clusters()`
reimplemented that resolution and had none of them — and `sleep()`, the documented idle call and an
MCP tool, calls `consolidate_clusters()` unconditionally.

So a user who turns on the hardening flags precisely because they fear single-shot poison had them
on the path they call explicitly and OFF the path that runs on idle. Measured 2026-08-15, same
corpus, same flags — four divergences, not one:

    consolidate()  corroboration=True  -> honest fact ACTIVE      sleep() -> SUPERSEDED
    consolidate()  persistence=3       -> honest fact ACTIVE      sleep() -> SUPERSEDED
    consolidate()  back-filled record  -> correctly retired       sleep() -> RETIRED THE CURRENT ONE
    consolidate()  refused overturn    -> reopened queue = 2      sleep() -> queue = 0

Copying the corroboration `if` across would have closed a quarter of it and read as complete, so
the fix is an EXTRACTION: `_resolve_state_toggle` is the one place that decides, and a guard added
to it in a year cannot land on one path only.

EVERY TEST HERE IS PARAMETRISED OVER BOTH ENTRY POINTS. Testing the fix on `consolidate()` alone
would pass on the build where the bug lives.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from inspeximus import Inspeximus

HONEST = "The deployment retry limit is 3 for the billing service pipeline"
POISON = "The deployment retry limit is 12 for the billing service pipeline"

#: The two ways a store consolidates. `sleep` is the one that runs unattended.
PATHS = [("consolidate", lambda ix: ix.consolidate()), ("sleep", lambda ix: ix.sleep())]


def _store(**flags):
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    for k, v in flags.items():
        setattr(ix, k, v)
    # A ripe cluster: consolidate_clusters needs >= 15 similar members before it fires at all.
    for i in range(16):
        ix.remember(f"{HONEST} note {i}")
    return ix


def _id(r):
    return r if isinstance(r, str) else r["id"]


def _status(ix, mid):
    return next((r["status"] for r in ix.items if r["id"] == mid), "gone")


@pytest.mark.parametrize("name,run", PATHS)
@pytest.mark.parametrize("flag", ["supersede_requires_corroboration", "supersede_persistence"])
def test_a_single_uncorroborated_contradiction_cannot_retire_a_standing_fact(name, run, flag):
    ix = _store(**{flag: True if flag.endswith("corroboration") else 3})
    honest = _id(ix.remember(HONEST))
    ix.remember(POISON)
    run(ix)
    assert _status(ix, honest) == "active", f"{name} retired the standing fact with {flag} on"


@pytest.mark.parametrize("name,run", PATHS)
@pytest.mark.parametrize("flag", ["supersede_requires_corroboration", "supersede_persistence"])
def test_control_with_the_guard_OFF_the_contradiction_does_win(name, run, flag):
    """THE MUST-FAIL CONTROL. Without it, "the honest fact survived" could equally mean the toggle
    pass never fired -- which is how a guard that does nothing passes its own test."""
    ix = _store()
    honest = _id(ix.remember(HONEST))
    ix.remember(POISON)
    run(ix)
    assert _status(ix, honest) == "superseded", \
        f"{name} did not toggle at all, so the {flag} test above measures nothing"


@pytest.mark.parametrize("name,run", PATHS)
def test_a_back_filled_record_does_not_overwrite_the_current_one(name, run):
    """Ordering by ingest time instead of validity time INVERTS the bi-temporal rule: a fact learned
    late about an earlier state retires the genuinely-current one. The cluster path used `ts`."""
    ix = _store()
    now = time.time()
    current = _id(ix.remember(HONEST, valid_from=now))
    backfill = _id(ix.remember(POISON, valid_from=now - 365 * 86400))
    run(ix)
    assert _status(ix, current) == "active", f"{name} retired the current value"
    assert _status(ix, backfill) == "superseded", f"{name} left the back-filled record standing"


@pytest.mark.parametrize("name,run", PATHS)
def test_a_refused_overturn_is_visible_to_the_reader(name, run):
    """Refusing the overturn is only half the job. Before this, a consumer calling plain recall() saw
    the correct live value and NOTHING ELSE on the cluster path -- the only trace was an unlabelled
    link, indistinguishable from any other."""
    ix = _store(supersede_requires_corroboration=True)
    ix.remember(HONEST)
    ix.remember(POISON)
    run(ix)
    assert ix.reopened(), f"{name} refused the overturn silently"


def test_there_is_exactly_one_resolution_in_the_source():
    """THE test that matters in a year. Two copies of one decision is how they diverged in the first
    place; this fails if a second inlined resolution reappears on either path."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    assert src.count('om["superseded_by_policy"] = "state_toggle"') <= 1, \
        "the state-toggle resolution is inlined in more than one place again"
    assert src.count("def _resolve_state_toggle") == 1
    assert src.count("self._resolve_state_toggle(") == 2, \
        "both consolidate() and consolidate_clusters() must go through the shared resolution"
