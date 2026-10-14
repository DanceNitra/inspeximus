"""3.20, F8b (the class of F8): a recall query cannot be recovered from the records written after it.

THE DEFECT. With observe_recall on, every write that follows a recall carries `recall_window.q`, and q was the first
48 bits of an unsalted sha256 of the query. The record is durable, so a query holding an e-mail address or a name was
recovered from the store by hashing a list of candidates. Same class as F8, in the store instead of a report.

WHAT IS PINNED HERE. q is an HMAC of the query under the store's read-guard key (the key home, never beside the
store), under its own domain label, still 12 hex characters. Two handles on the same store, which is what two
processes are, stamp the same q for the same query, because the analysis q exists for groups the writes that
followed one recall across the whole store. A store with no path uses a key for its process.

KNOWN LIMIT, stated and not fixed in 3.20: values written before 3.20 keep the unsalted hash. Re-keying them is a
rewrite of the store; it is a backlog item for an opt-in tool.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus.core import Inspeximus  # noqa: E402

QUERY = "what is alice@example.com's phone number"
CANDIDATES = ["what is bob@example.com's phone number", "what is carol@example.com's phone number", QUERY]


def _unsalted(q):
    return hashlib.sha256(q.encode("utf-8", "replace")).hexdigest()[:12]


def _stamp(m, query):
    m.recall(query, k=3)
    rid = m.remember("noted after the lookup")
    return next(r for r in m.items if r["id"] == rid)["recall_window"]["q"]


def _store(path):
    m = Inspeximus(str(path), observe_recall=True)
    if not any(r.get("key") == "alice::phone" for r in m.items):
        m.remember("alice's phone is +100", key="alice::phone")
    return m


def test_a_query_holding_an_address_is_not_recovered_by_trying_candidates(tmp_path):
    q = _stamp(_store(tmp_path / "s.json"), QUERY)
    assert len(q) == 12 and all(c in "0123456789abcdef" for c in q), "the field keeps its 12-hex shape"
    assert q not in {_unsalted(c) for c in CANDIDATES}, "q is the unsalted sha256 of the query"


def test_two_handles_on_one_store_stamp_the_same_q_for_the_same_query(tmp_path):
    a = _stamp(_store(tmp_path / "s.json"), QUERY)
    b = _stamp(_store(tmp_path / "s.json"), QUERY)
    assert a == b, "the analysis that groups writes by recall works across processes"
    assert _stamp(_store(tmp_path / "s.json"), "alice phone, asked another way") != a


def test_another_store_stamps_another_q(tmp_path):
    a = _stamp(_store(tmp_path / "a.json"), QUERY)
    b = _stamp(_store(tmp_path / "b.json"), QUERY)
    assert a != b, "q is keyed per store"


def test_a_store_with_no_path_still_stamps_a_keyed_q():
    m = Inspeximus(None, observe_recall=True)
    m.remember("alice's phone is +100", key="alice::phone")
    q = _stamp(m, QUERY)
    assert len(q) == 12 and q != _unsalted(QUERY)
