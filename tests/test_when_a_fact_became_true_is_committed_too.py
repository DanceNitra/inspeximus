"""`valid_from` decides every `as_of()` answer and was in no commitment at all.

`as_of()` is how you answer "what did the agent believe when it acted, provably" — the query the
bitemporal machinery exists for. Two fields decide it, and neither was bound:

  * `valid_from` — when the fact became true. Write-once, and now committed as `time_sha256`.
  * `invalidated_at` — when it stopped being current. Written MECHANICALLY by supersession, so
    committing it would demand an amendment per retirement — the churn argument that shaped
    `status_sha256`. Handled by making it non-load-bearing instead: `as_of()` DERIVES the interval
    end from `valid_from` ordering, which the `as_recorded` branch already did. Two implementations
    of one rule, and only one of them was safe.

THE CONTROL THAT MADE THIS SPECIFIC. `state_digest` covers the sibling field `ts` — editing `ts`
changes the digest, so a client-held witness catches it. It simply did not cover the two fields that
answer the temporal question. Without that control, "the witness is blind here" could have meant
"the witness is blind generally", which is a different and much larger claim.

MEASURED HONESTLY, including what did NOT reproduce. Editing `valid_from` ALONE did not flip the
answer in this fixture — it takes BOTH edits, `net90.valid_from` earlier and `net30.invalidated_at`
cut short, to move the 2024-06 answer from net30 to net90. Either single edit blanked the answer
instead, which is a denial rather than a substitution. The integrity gap was real and reproducible
on its own; the behavioural flip needed the pair.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from inspeximus import Inspeximus

T24 = time.mktime((2024, 1, 1, 0, 0, 0, 0, 1, -1))
T25 = time.mktime((2025, 1, 1, 0, 0, 0, 0, 1, -1))
Q24 = time.mktime((2024, 6, 1, 0, 0, 0, 0, 1, -1))
Q25 = time.mktime((2025, 6, 1, 0, 0, 0, 0, 1, -1))


def _terms():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("payment terms are net30", key="terms", object="net30", valid_from=T24)
    ix.remember("payment terms are net90", key="terms", object="net90", valid_from=T25)
    ix.flush()
    return p, ix


def _obj(a):
    return a.get("object") if isinstance(a, dict) else a


def _edit(p, fn):
    rows = json.load(open(p, encoding="utf-8"))
    fn(rows)
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return Inspeximus(path=p, receipts=True)


def test_the_honest_history_reads_back():
    """First, because every test below is meaningless if the baseline is wrong."""
    _p, ix = _terms()
    assert _obj(ix.as_of("terms", Q24)) == "net30"
    assert _obj(ix.as_of("terms", Q25)) == "net90"
    assert ix.verify_writes() == (True, [])


def test_rewriting_when_a_fact_became_true_is_caught():
    p, _ = _terms()
    ix = _edit(p, lambda rows: [r.update(valid_from=T24 - 30 * 86400)
                                for r in rows if r.get("object") == "net90"])
    ok, problems = ix.verify_writes()
    assert not ok and any("valid_from" in x for x in problems), problems


def test_rewriting_where_that_time_came_from_is_caught():
    """`valid_from_source` rides in the same hash. It is set INTERNALLY -- "declared" when the caller
    passed a `valid_from`, absent when the store defaulted it -- which is exactly why it is worth
    binding: it is the store's own claim about whether anyone asserted that time, and a claim whose
    provenance can be rewritten silently is not evidence. Flipping it on disk downgrades a declared
    event time to an inferred one, or upgrades a defaulted one to a declared one."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("terms are net30", key="terms", object="net30", valid_from=T24)
    ix.flush()
    assert json.load(open(p, encoding="utf-8"))[0].get("valid_from_source") == "declared", \
        "the fixture no longer produces the field this test is about"
    ix2 = _edit(p, lambda rows: rows[0].pop("valid_from_source", None))
    ok, problems = ix2.verify_writes()
    assert not ok and any("time came from" in x for x in problems), problems


def test_the_pair_of_edits_that_actually_moved_the_answer_no_longer_does():
    """THE attack, reproduced exactly. Both fields at once: net90 back-dated, net30 cut short."""
    p, _ = _terms()
    ix = _edit(p, lambda rows: [r.update(valid_from=T24 - 30 * 86400) if r.get("object") == "net90"
                                else r.update(invalidated_at=T24) for r in rows])
    assert _obj(ix.as_of("terms", Q24)) == "net30", "the store's belief at a past date was rewritten"
    assert not ix.verify_writes()[0]


@pytest.mark.parametrize("name,mut", [
    ("cut short", lambda rows: [r.update(invalidated_at=T24) for r in rows if r.get("object") == "net30"]),
    ("wiped", lambda rows: [r.pop("invalidated_at", None) for r in rows]),
    ("far future", lambda rows: [r.update(invalidated_at=T25 + 9e7) for r in rows]),
])
def test_the_stored_interval_end_no_longer_decides_anything(name, mut):
    """`invalidated_at` cannot be committed without churning the chain, so it was made
    non-load-bearing instead: the answer is derived from `valid_from` ordering. These edits are
    therefore NOT reported as tampering -- correctly, because they now change nothing."""
    p, _ = _terms()
    ix = _edit(p, mut)
    assert _obj(ix.as_of("terms", Q24)) == "net30", f"{name} still moved the answer"
    assert _obj(ix.as_of("terms", Q25)) == "net90", f"{name} still moved the answer"


def test_control_the_digest_does_cover_a_sibling_timestamp():
    """The control that made the original finding specific rather than a claim that the witness is
    blind generally. If this stops holding, `state_digest` has a much bigger hole than this file."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("x", key="k", object="v")
    ix.flush()
    before = ix.state_digest()
    ix2 = _edit(p, lambda rows: rows[0].update(ts=rows[0]["ts"] - 999))
    assert ix2.state_digest() != before


def test_the_bitemporal_branch_still_answers_as_it_did():
    """`as_recorded` already derived its interval end; unifying the default branch onto the same rule
    must not have moved it. A correction recorded LATER cannot leak into the earlier belief."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("terms are net30", key="terms", object="net30", valid_from=T24)
    t_before_correction = time.time()
    time.sleep(0.01)
    ix.remember("terms are net90", key="terms", object="net90", valid_from=T25)
    assert _obj(ix.as_of("terms", Q25, as_recorded=t_before_correction)) == "net30"
    assert _obj(ix.as_of("terms", Q25)) == "net90"


def test_a_receipt_written_before_this_field_existed_does_not_alarm():
    """Same forward-compatibility every added commitment gets: old receipts lack the key and are
    checked on what they do commit to."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("a record from an older version", key="k", object="v")
    ix.flush()
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    for r in (rec if isinstance(rec, list) else rec.get("receipts")):
        r.get("commit", {}).pop("time_sha256", None)
    json.dump(rec, open(rp, "w", encoding="utf-8"))
    _ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert [x for x in problems if "valid_from" in x] == [], problems
