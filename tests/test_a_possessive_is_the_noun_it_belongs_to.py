"""A possessive tokenizes to its noun, so "alice's phone" is found by "alice phone".

Found 2026-09-16: `rectify` wrote "alice's phone is +200" and `recall("alice phone")` scored it level
with "alice prefers email" and "bob's phone is +300". The stemmer folded "alice's" to "alice'", one
token the query never contains, so the rectified record matched on `phone` alone and its rank was
decided by a decay factor that differs at 1e-6 per second between memory types. The test passed only
when the three writes landed within one second.

Controls: the fold does not reach a contraction that is not a possessive, a plural that is not a
possessive still folds as before, and the rank is measured with the writes a second apart, which is
the condition that exposed the defect.
"""
import time

from inspeximus import Inspeximus
from inspeximus.core import _tokens


def test_a_possessive_and_its_noun_are_one_token():
    assert _tokens("alice's phone") == {"alice", "phone"}
    assert _tokens("bob's phone") == {"bob", "phone"}
    assert _tokens("the agents' phones") == {"agent", "phone"}
    # a plural still folds, and a stem that is not a possessive is untouched
    assert _tokens("phones") == {"phone"}
    assert _tokens("don't panic") == {"don't", "panic"}


def test_the_rectified_record_outranks_unrelated_records_with_writes_a_second_apart(tmp_path):
    from inspeximus.subject_rights import rectify
    m = Inspeximus(str(tmp_path / "mem.json"))
    m.remember("alice's phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    time.sleep(1.05)
    m.remember("alice prefers email", source={"doc": "crm/alice"})
    time.sleep(1.05)
    m.remember("bob's phone is +300", key="bob::phone", source={"doc": "crm/bob"})
    time.sleep(1.05)
    rectify(m, key="alice::phone", text="alice's phone is +200", actor="dpo", reason="DSAR-17",
            subject="crm/alice")
    hits = m.recall("alice phone", k=3, observe=False)
    assert hits[0]["text"] == "alice's phone is +200"
    # not a tie: the corrected record matches both query tokens, the others one each
    assert hits[0]["score"] > hits[1]["score"]
