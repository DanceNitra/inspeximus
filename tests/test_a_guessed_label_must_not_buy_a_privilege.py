"""Two ways a caller held rows outside a bound the operator set, and aimed the delete at real ones.

MTYPE-AIM. `_infer_type()` derives `mtype` from text; `mtype` picks the decay half-life (7 days
episodic vs 3650 days procedural, a 521x spread); `_effective_value()` ranks capacity eviction by it.
So a label guessed from CALLER TEXT sets its own retention class, and one word inside a document the
agent reads decides which of the user's true memories get hard-deleted.

    _infer_type('attacker note about vendor onboarding')          -> episodic
    _infer_type('always attacker note about vendor onboarding')   -> procedural

AGE IS A PRECONDITION, and finding that out is most of the work here. My first harness wrote
everything within milliseconds, so the decay factor was ~1.0 under BOTH half-lives and the arms came
out identical -- I read that as "the fix works" until the must-fail control also came out identical,
which means the harness was reproducing nothing. With one day of ageing per round:

    days | legacy control | legacy attack | fixed control | fixed attack   (honest/attacker evicted)
      3  |      2/2       |      3/1      |      2/2      |     2/2
      6  |      8/8       |      9/7      |      8/8      |     8/8

One word moves two evictions from the attacker's records onto the user's, at every scale. (Smaller
than the scan's inversion on its own fixture; same direction, and consistent.)

Tightening the regex is unwinnable -- any keyword list over attacker-controlled text is bypassable --
and it is not the defect. The defect is that a GUESS confers a PRIVILEGE. `mtype_inferred` records
which it was; inference still informs ranking, but an inferred type decays at the episodic rate.

GUARD-KEY. `_GUARD_KEYSPACES` holds two prefixes and `remember()` reserved one. Records under the
other were excluded from the capacity population BEFORE the comparison and from the consolidation
keep-budget, while still being rankable by recall: 30 active on a store capped at 10.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.code_guard import deprecate_symbol
from inspeximus.core import _infer_type

DAY = 86400.0
HONEST = "the vendor onboarding checklist step {} is filed with legal"
ATTACK = "{}attacker note {} about vendor onboarding"


def _evictions(marker: str, days: int, legacy: bool) -> tuple:
    """Returns (honest evicted, attacker evicted). `legacy=True` strips the new field to restore the
    pre-fix behaviour -- this is the must-fail control, not a convenience."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=8)
    honest, attacker = [], []
    now = time.time()
    for n in range(days):
        for i in range(2):
            h = ix.remember(HONEST.format(f"{n}{i}"))
            a = ix.remember(ATTACK.format(marker, f"{n}{i}"))
            honest.append(h if isinstance(h, str) else h["id"])
            attacker.append(a if isinstance(a, str) else a["id"])
        for r in ix._items:                    # age one day, so the two half-lives can differ at all
            r["ts"] -= DAY
            r["last_access"] = r.get("last_access", now) - DAY
            if legacy:
                r.pop("mtype_inferred", None)
    live = {r["id"] for r in ix.items}
    return (sum(1 for i in honest if i not in live), sum(1 for i in attacker if i not in live))


def test_the_marker_word_does_change_the_inferred_type():
    """The premise, checked first. If inference stopped differentiating these two strings, every
    measurement below would be symmetric for a reason that has nothing to do with the fix."""
    assert _infer_type("attacker note about vendor onboarding") == "episodic"
    assert _infer_type("always attacker note about vendor onboarding") == "procedural"


@pytest.mark.parametrize("days", [3, 4, 6])
def test_control_the_attack_DOES_land_on_the_pre_fix_behaviour(days):
    """MUST-FAIL CONTROL, and it earned its place: without it I had a green result from a harness
    that wrote every record within milliseconds, where the decay factor is ~1.0 under both
    half-lives and no differential can exist."""
    ch, ca = _evictions("", days, legacy=True)
    ah, aa = _evictions("always ", days, legacy=True)
    assert (ch, ca) == (ah - 1, aa + 1), (
        f"the harness is not reproducing the finding: control {ch}/{ca}, attack {ah}/{aa}")


@pytest.mark.parametrize("days", [3, 4, 6])
def test_word_choice_in_untrusted_text_no_longer_aims_the_eviction(days):
    assert _evictions("", days, legacy=False) == _evictions("always ", days, legacy=False)


def test_an_inferred_type_loses_its_half_life_IN_THE_EVICTION_PATH_ONLY():
    """The mechanism, directly, and the boundary is the point.

    A declared type is the caller's statement about their own data and keeps its half-life
    everywhere. A guessed one keeps it for RANKING -- inference is genuinely useful there -- and
    loses it where it decides what gets DELETED.

    The first version of this fix applied the downgrade inside `_effective_value` itself, which
    ranking also uses. Every inferred record then decayed at one rate, a time gap stopped reordering
    anything, and `test_a_time_gap_moves_only_ties` caught it via its own vacuity guard -- a test
    written to notice when an assertion has stopped being able to fail.
    """
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    guessed = ix.remember("always about vendor onboarding")
    declared = ix.remember("a note about vendor onboarding", mtype="procedural")
    rows = {r["id"]: r for r in ix.items}
    g = rows[guessed if isinstance(guessed, str) else guessed["id"]]
    d = rows[declared if isinstance(declared, str) else declared["id"]]
    assert g["mtype"] == d["mtype"] == "procedural"
    assert g["mtype_inferred"] is True and d["mtype_inferred"] is False

    later = time.time() + 30 * DAY
    assert ix._eviction_value(g, later) < 0.2 < ix._eviction_value(d, later), \
        "the guess still buys a retention privilege where it decides deletions"
    assert ix._effective_value(g, later) == ix._effective_value(d, later), \
        "the downgrade leaked into ranking, which flattens real signal for no security gain"


# ─────────────────────────────────────────────────────────── the reserved keyspace
def test_an_arbitrary_writer_cannot_mint_the_guard_keyspace():
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=10)
    with pytest.raises(ValueError, match="reserved code-guard keyspace"):
        ix.remember("ATTACKER PAYLOAD", key="code::symbol::pwn")


def test_the_legitimate_writer_still_can():
    """The must-not-brick control: a reservation that also blocks the feature is not a fix."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=10)
    deprecate_symbol(ix, "old_fn", "new_fn")
    assert [r for r in ix.items if (r.get("key") or "").startswith("code::symbol::")]


def test_the_cap_holds_against_the_prefix():
    """The harm, not just the door: guard records are exempt from the capacity population, so an
    unreserved prefix meant unbounded growth on a store whose owner set a cap."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), capacity=10)
    for i in range(30):
        with pytest.raises(ValueError):
            ix.remember(f"ATTACKER PAYLOAD {i}", key=f"code::symbol::pwn{i}")
    assert len([r for r in ix.items if r.get("status") == "active"]) <= 10


def test_both_guard_prefixes_are_reserved_now():
    """The class, not the instance. `_GUARD_KEYSPACES` lists what is exempt from housekeeping; every
    entry must also be un-mintable through remember(), or the exemption is a public API."""
    from inspeximus.core import _GUARD_KEYSPACES
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    for prefix in _GUARD_KEYSPACES:
        with pytest.raises(ValueError, match="reserved"):
            ix.remember("probe", key=f"{prefix}probe")
