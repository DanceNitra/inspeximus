"""A state toggle must never retire a keyed record because of a record with a DIFFERENT key.

THE INCIDENT, measured on 2026-09-28 on a copy of the Crew OS store (11,331 records). The crew daemon
calls `sleep(cluster_threshold=15)` every tenth tick. The first sleep that completed after a restart
on 2026-09-27 retired 426 records through `meta.superseded_by_policy == "state_toggle"`, and every
one of them was keyed and was retired by a record with another key. 389 were the persona layers
`crew-os::persona::<agent>::<layer>` of 23 agents, about 17 layers each. The example that was
reported: `crew-os::persona::55-centurion::weaknesses` was retired by
`crew-os::persona::55-centurion::agent-bus`. After that, `current()` answered None for those keys and
`recall` no longer returned them. Nothing had retired them on purpose.

WHY IT HAPPENED. The layers were written from one template, so every pair clears the 0.82
near-duplicate bar. A layer that says "never" against one that does not is a negation clash, and
"agent 41" against "agent 42" in the same sentence is a value clash (exactly one number differs).
The toggle then retires the older record as if the newer one had corrected it. For unkeyed notes that
is the design: "I like coffee" -> "I do not like coffee" is a preference flip. A keyed record is
different. It is the current value of its key, and a keyed write with the same key is the only thing
that replaces it. A toggle from another key leaves the key with no current value, which is `retire()`
without a reason, done by a heuristic.

Replayed on the same copy with every toggle retirement undone first: `sleep(cluster_threshold=15)`
retired 446 records on 3.15.1, all of them keyed records retired by another key, 402 of them persona
layers. With this rule it retires none of them, and the pairs are linked like any other near
duplicate.

THE RULE. The toggle may retire the older record only when the older one has no key, or when the
newer one carries the same key in the same tenant. A keyed record paired with another key, or with
an unkeyed record, is linked, not retired, and the pass counts it under `distinct_keys`.

EVERY TEST IS PARAMETRISED OVER BOTH ENTRY POINTS, because `consolidate()` and `sleep()` reach the
toggle through different loops (see test_the_idle_path_runs_the_same_guards.py). The controls prove
that the fixtures reach the toggle path at all and that the rule is not "keyed records never toggle":
the same texts without keys are still toggled, a keyed record still replaces an older unkeyed note,
and two values of ONE key still toggle.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus  # noqa: E402

#: The two ways a store consolidates. `sleep` is the one the crew daemon runs unattended.
PATHS = [("consolidate", lambda ix: ix.consolidate()),
         ("sleep", lambda ix: ix.sleep(cluster_threshold=15))]
#: The two-record fixtures below: a cluster of two is ripe at threshold 2.
PAIR_PATHS = [("consolidate", lambda ix: ix.consolidate()),
              ("sleep", lambda ix: ix.sleep(cluster_threshold=2))]

#: Eight agents, two layers each: sixteen records, so the sleep path's cluster is ripe (>= 15).
AGENTS = [f"{n}-agent" for n in range(41, 49)]
LAYERS = {"gates": "", "weaknesses": "never "}


def _layer_text(agent: str, layer: str) -> str:
    """One shared template, like the persona cards: every pair is a near duplicate, the two layers
    of one agent are a negation clash, and the same layer of two agents is a value clash."""
    n = agent.split("-")[0]
    return (f"Crew agent {n} persona card. The agent {LAYERS[layer]}ships a render to the "
            f"channel without the verifier signing the render first.")


def _templated_store(keyed: bool) -> tuple[Inspeximus, dict]:
    m = Inspeximus(path=None)
    ids = {}
    for agent in AGENTS:
        for layer in LAYERS:
            key = f"crew::persona::{agent}::{layer}" if keyed else None
            ids[(agent, layer)] = m.remember(_layer_text(agent, layer), key=key)
    return m, ids


def _status(m, rid):
    return next(r for r in m.items if r["id"] == rid)["status"]


def _report(name, rep):
    return rep["consolidated_clusters"] if name == "sleep" else rep


@pytest.mark.parametrize("name,run", PATHS)
def test_templated_keyed_layers_all_keep_their_current_value(name, run):
    """THE DEFECT. Sixteen keys, sixteen facts; after the pass every key still answers."""
    m, ids = _templated_store(keyed=True)
    rep = _report(name, run(m))
    retired = sorted(f"{a}::{layer}" for (a, layer), rid in ids.items() if _status(m, rid) != "active")
    assert not retired, (
        f"{name} retired {len(retired)} keyed records because of records with other keys: {retired}")
    missing = [k for k in (f"crew::persona::{a}::{layer}" for a in AGENTS for layer in LAYERS)
               if m.current(k) is None]
    assert not missing, f"{name} left {len(missing)} keys with no current value: {missing}"
    assert rep["toggled"] == 0, f"{name} reported toggles between distinct keys: {rep}"


@pytest.mark.parametrize("name,run", PATHS)
def test_the_pass_reports_the_pairs_it_left_standing(name, run):
    """An operator reading the report has to be able to tell "no clash found" from "clashes found and
    left alone because the keys differ". The second is what happened here, so it gets a count."""
    m, _ = _templated_store(keyed=True)
    rep = _report(name, run(m))
    assert rep.get("distinct_keys", 0) > 0, (
        f"{name} found clashing pairs between distinct keys and did not report them: {rep}")


@pytest.mark.parametrize("name,run", PATHS)
def test_control_the_same_texts_without_keys_still_toggle(name, run):
    """THE MUST-FAIL CONTROL. If these did not toggle either, the fixture would never reach the
    toggle path and the two tests above would pass on the build that has the defect."""
    m, _ = _templated_store(keyed=False)
    rep = _report(name, run(m))
    assert rep["toggled"] > 0, f"{name} did not toggle unkeyed clashing notes; the fixture measures nothing"


STANDING = "the office printer is on floor 3 of the east wing"
CONTRADICTION = "the office printer is not on floor 3 of the east wing"


def _policy(m, rid):
    return (next(r for r in m.items if r["id"] == rid).get("meta") or {}).get("superseded_by_policy")


@pytest.mark.parametrize("name,run", PAIR_PATHS)
def test_an_unkeyed_note_does_not_end_a_keyed_value(name, run):
    """Keyed older, unkeyed newer. The note may be right, but it does not carry the key, so retiring
    the keyed record would leave `current(key)` empty. The keyed write is the way to change a key."""
    m = Inspeximus(path=None)
    standing = m.remember(STANDING, key="printer::floor")
    note = m.remember(CONTRADICTION)
    rep = _report(name, run(m))
    assert _status(m, standing) == "active", "an unkeyed note ended a keyed value"
    assert m.current("printer::floor") is not None
    assert _status(m, note) == "active"
    assert rep["distinct_keys"] == 1 and rep["toggled"] == 0, rep


@pytest.mark.parametrize("name,run", PAIR_PATHS)
def test_control_a_keyed_record_still_replaces_an_unkeyed_note(name, run):
    """Unkeyed older, keyed newer: no key loses its value, so the toggle works as it always did.
    Without this control the rule could be "keyed records never toggle", which would also pass above."""
    m = Inspeximus(path=None)
    note = m.remember(STANDING)
    keyed = m.remember(CONTRADICTION, key="printer::floor")
    rep = _report(name, run(m))
    assert _status(m, note) == "superseded", "a keyed record no longer replaces the older note it contradicts"
    assert _policy(m, note) == "state_toggle"
    assert _status(m, keyed) == "active"
    assert rep["toggled"] == 1, rep


@pytest.mark.parametrize("name,run", PAIR_PATHS)
def test_control_two_values_of_one_key_still_toggle(name, run):
    """A genuine same-key state toggle still works. Two agent-bound handles each keep their own
    active record for one key (write isolation, see _supersede_by_key), so the operator's view holds
    two values of the same fact, and the newer one retires the older. Without this control the rule
    could be "keyed records never toggle", which would pass every defect test above."""
    m = Inspeximus(path=None)
    old = m.as_agent("alice").remember(STANDING, key="printer::floor")
    new = m.as_agent("bob").remember(CONTRADICTION, key="printer::floor")
    assert _status(m, old) == "active" and _status(m, new) == "active", \
        "precondition: both values of the key must be active before the pass"
    rep = _report(name, run(m))
    assert _status(m, old) == "superseded" and _policy(m, old) == "state_toggle", \
        f"the older value of ONE key was not toggled: {_status(m, old)}, {_policy(m, old)}"
    assert _status(m, new) == "active"
    assert rep["toggled"] == 1 and rep.get("distinct_keys", 0) == 0, rep


@pytest.mark.parametrize("name,run", PAIR_PATHS)
def test_the_same_key_in_two_tenants_is_two_facts(name, run):
    """Write-time supersession already treats a key as (tenant, key): "only same-tenant records
    collide on a key". An unbound store consolidates across tenants, so the toggle must use the same
    identity or one tenant's value ends another tenant's."""
    m = Inspeximus(path=None)
    a = m.for_tenant("acme").remember(STANDING, key="printer::floor")
    b = m.for_tenant("globex").remember(CONTRADICTION, key="printer::floor")
    run(m)
    assert _status(m, a) == "active" and _status(m, b) == "active", \
        "one tenant's keyed value ended the other tenant's value for the same key string"
