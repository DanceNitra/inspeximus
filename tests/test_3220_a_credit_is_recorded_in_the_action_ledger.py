"""credit() records itself in the action ledger when one is passed (3.22, owner 1a on 2026-10-20).

credit's own docstring measured the gap: a credit changes good and bad, which decide what the influence gate serves,
yet it left no receipt, state_digest did not move and verify_writes stayed clean. A trading bot's outcome is the
first thing an auditor asks about (agora_output/strategy/ftmo_decision_log_on_inspeximus_2026-10-20.md, section 2).
"""
import pytest

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger


@pytest.fixture
def store(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"))
    led = ActionLedger(m, path=str(tmp_path / "actions.json"), actor="bot")
    rid = m.remember("decision d-1: take the trade", source={"doc": "bot:d-1"})
    return m, led, rid


def test_a_credit_is_one_ledger_entry_with_its_inputs_and_the_counts_it_left(store):
    m, led, rid = store
    r = m.credit([rid], outcome="good", weight=2.0, warrant="broker:close:d-1", ledger=led)
    e = led.entries()[-1]
    assert len(led.entries()) == 1 and e["action"] == "memory:credit"
    inputs = {"ids": [rid], "outcome": "good", "weight": 2.0, "warrant": "broker:close:d-1"}
    counts = {rid: {"good": 2.0, "bad": 0.0, "good_warranted": 2.0}}
    m2 = led.matches(e["seq"], inputs, dict(r, counts=counts))
    assert m2["inputs"] is True and m2["output"] is True
    assert led.verify(bind_to_store=False) == (True, [])


def test_a_changed_warrant_or_count_no_longer_matches(store):
    m, led, rid = store
    r = m.credit(rid, outcome=True, warrant="broker", ledger=led)
    seq = led.entries()[-1]["seq"]
    inputs = {"ids": [rid], "outcome": True, "weight": 1.0, "warrant": "broker"}
    assert led.matches(seq, inputs=inputs)["inputs"] is True
    assert led.matches(seq, inputs=dict(inputs, warrant="broker2"))["inputs"] is False
    counts = {rid: {"good": 1.0, "bad": 0.0, "good_warranted": 1.0}}
    assert led.matches(seq, output=dict(r, counts=counts))["output"] is True
    assert led.matches(seq, output=dict(r, counts={rid: dict(counts[rid], good=9.0)}))["output"] is False


def test_a_refused_credit_records_nothing(store):
    m, led, rid = store
    with pytest.raises(ValueError):
        m.credit([rid], outcome="good", weight=-1, ledger=led)
    assert led.entries() == []


def test_without_a_ledger_nothing_is_recorded(store):
    m, led, rid = store
    m.credit([rid], outcome="good")
    assert led.entries() == []

def test_a_ledger_that_fails_to_record_does_not_undo_the_credit_and_says_so(store):
    m, led, rid = store

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")
    with pytest.raises(OSError) as info:
        m.credit([rid], outcome="good", ledger=Broken())
    assert info.value.credit_applied["updated"] == [rid]
    assert next(r for r in m.items if r["id"] == rid)["good"] == 1.0


def test_ids_from_a_generator_are_the_ids_the_entry_records(store):
    """AUDIT-A F-8: the ledger read the ids a second time, after the loop had used up the generator."""
    m, led, rid = store
    rid2 = m.remember("decision d-2: skip the trade", source={"doc": "bot:d-2"})
    r = m.credit((i for i in [rid, rid2]), outcome="good", ledger=led)
    assert r["updated"] == [rid, rid2]
    e = led.entries()[-1]
    inputs = {"ids": [rid, rid2], "outcome": "good", "weight": 1.0, "warrant": None}
    assert led.matches(e["seq"], inputs=inputs)["inputs"] is True
    # CONTROL: the entry is not one that matches any list of ids.
    assert led.matches(e["seq"], inputs=dict(inputs, ids=[]))["inputs"] is False
