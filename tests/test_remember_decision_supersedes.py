"""`remember_decision` kept every decision on a topic ACTIVE at once.

Its docstring promises the product's whole thesis, applied to decisions:

    "`topic` (recommended) becomes a deterministic supersession key `decision::<topic>` — so a NEW
     decision on the same topic RETIRES the old one ... recall always returns the CURRENT decision ...
     and `revert('decision::<topic>')` restores the prior one."

None of it happened. `object` was set to the TOPIC:

    object=(topic.strip() if topic else None)

The topic is already the KEY. Passing it as the value too made every decision on a topic look like a
restatement of the same value, and keyed supersession is object-identity aware precisely so that a
paraphrase does not count as a correction -- so the second decision was read as a reaffirm and retired
nothing. Measured before the fix: two decisions on one topic left TWO active records, where plain
`remember(key=...)` left one.

It is exposed over MCP as `remember_decision`, so an agent asking "what did we decide about X" could be
handed two contradictory current answers.

Found by running the probes nothing cites: `remember_decision_probe.py` and
`distill_and_remember_probe.py` had been failing on exactly these assertions, and nothing executed them.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"))


def _active(m, key=None):
    return [r for r in m.items if r.get("status") == "active"
            and (key is None or r.get("key") == key)]


def test_a_new_decision_on_a_topic_retires_the_old_one():
    """THE defect, in the words of its own docstring."""
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    m.remember_decision("we will use SQLite instead", topic="database")

    active = _active(m, "decision::database")
    assert len(active) == 1, [r["text"] for r in active]
    assert "SQLite" in active[0]["text"]


def test_the_superseded_decision_is_retained_not_deleted():
    """A ledger, not an overwrite: the reversal has to stay attributable."""
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    m.remember_decision("we will use SQLite instead", topic="database")

    history = m.history("decision::database")
    assert [h["status"] for h in history] == ["superseded", "active"], history
    assert "Postgres" in history[0]["text"]


def test_revert_restores_the_prior_decision():
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    m.remember_decision("we will use SQLite instead", topic="database")

    res = m.revert("decision::database")
    assert res["ok"] is True
    assert res["reverted_to_object"] == "we will use Postgres"
    active = _active(m, "decision::database")
    assert len(active) == 1 and "Postgres" in active[0]["text"]


def test_the_value_committed_is_the_decision_not_the_topic():
    """The root cause, asserted directly: `object` is what supersession, the echo guard and revert() all
    key on. With the topic there, every decision on that topic is the 'same value'."""
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    rec = _active(m, "decision::database")[0]
    assert rec["object"] == "we will use Postgres"
    assert rec["object"] != "database"


def test_three_decisions_leave_exactly_one_current():
    """One swap could pass by accident; a chain cannot."""
    m = _store()
    for d in ("use Postgres", "use SQLite instead", "use DuckDB after all"):
        m.remember_decision(d, topic="database")

    active = _active(m, "decision::database")
    assert len(active) == 1 and "DuckDB" in active[0]["text"]
    assert len(m.history("decision::database")) == 3


def test_decisions_on_different_topics_do_not_retire_each_other():
    """The other direction. A supersession that fired too widely would 'fix' this test while destroying
    the feature -- one decision would silently retire an unrelated one."""
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    m.remember_decision("we will deploy on Fridays", topic="release-cadence")

    assert len(_active(m)) == 2
    assert len(_active(m, "decision::database")) == 1
    assert len(_active(m, "decision::release-cadence")) == 1


def test_a_topicless_decision_is_still_stored():
    """Without a topic there is no key, so nothing supersedes -- and that must keep working."""
    m = _store()
    m.remember_decision("we will revisit this next quarter")
    m.remember_decision("we will also review the budget")
    assert len(_active(m)) == 2


def test_re_asserting_the_SAME_decision_does_not_churn_the_ledger():
    """Object identity is why this is safe: repeating a decision verbatim is a reaffirm, not a reversal,
    and must not fill the history with fake changes of mind."""
    m = _store()
    m.remember_decision("we will use Postgres", topic="database")
    m.remember_decision("we will use Postgres", topic="database")

    assert len(_active(m, "decision::database")) == 1
    assert len(m.history("decision::database")) <= 2
