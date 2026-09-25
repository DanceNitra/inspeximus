"""`store.last_write` reports what the write became, on every path that retires or forks it on arrival.

Session T, item 4. `remember()` initialises `last_write` as {"status": "active"} before the supersession
pass, and each path that retires the incoming record is meant to overwrite it. The echo guard, the
objectless guard and the authority rule do. Two paths did not:

- keyed_lww_backfill: a keyed write whose `valid_from` is older than the current value is stored as
  history, `status="superseded"`, and `last_write` still said "active".
- a write below `fork_below` identity confidence is forked as `status="candidate"` before
  `last_write` is set, and `last_write` still said "active".

A caller that reads `last_write` to learn whether the value landed, as the docstring asks, was told it
had. The MCP and CLI write surfaces report the same field.
"""
from __future__ import annotations

import time

from inspeximus import Inspeximus


def _status(m, rid):
    return next(r["status"] for r in m.items if r["id"] == rid)


def test_a_backfilled_write_is_reported_as_superseded(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    now = time.time()
    cur = m.remember("the office is in Berlin", key="office", valid_from=now)
    assert m.last_write["status"] == "active", "control: an ordinary write lands"
    old = m.remember("the office is in Vienna", key="office", valid_from=now - 86400)
    assert _status(m, old) == "superseded", "control: the older fact is stored as history"
    assert _status(m, cur) == "active", "control: the current value is unchanged"
    lw = m.last_write
    assert lw["id"] == old and lw["status"] == "superseded", lw
    assert lw["policy"] == "keyed_lww_backfill" and lw["current_id"] == cur, lw
    assert lw["blocked"] is False, "a backfill is history by design, not a refusal"
    assert "previous" not in lw, "a write that did not land followed no value"


def test_a_forked_candidate_is_reported_as_a_candidate(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    cur = m.remember("the office is in Berlin", key="office")
    rid = m.remember("the office is in Vienna", key="office", identity_confidence=0.0)
    assert _status(m, rid) == "candidate", "control: the low-confidence write was forked"
    assert _status(m, cur) == "active", "control: the current value is unchanged"
    assert m.last_write["id"] == rid and m.last_write["status"] == "candidate", m.last_write


def test_last_write_status_matches_the_stored_record_on_every_keyed_path(tmp_path):
    """The class: whatever path a keyed write takes, `last_write["status"]` is the record's status."""
    m = Inspeximus(str(tmp_path / "mem.json"))
    now = time.time()
    calls = [
        dict(text="plan A", key="plan", valid_from=now),
        dict(text="plan B", key="plan", valid_from=now + 1),                 # lands
        dict(text="plan A", key="plan", valid_from=now + 2),                 # echo of a retired value
        dict(text="plan Z", key="plan", valid_from=now - 100),               # backfill
        dict(text="plan C", key="plan", identity_confidence=0.0),            # candidate
        dict(text="plan C", key="plan", valid_from=now + 3, reaffirm=True),  # lands
    ]
    for kw in calls:
        rid = m.remember(kw.pop("text"), **kw)
        assert m.last_write["id"] == rid
        assert m.last_write["status"] == _status(m, rid), (kw, m.last_write, _status(m, rid))
