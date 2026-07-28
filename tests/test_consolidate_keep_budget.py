"""consolidate(keep=N) emptied the store and reported that it had kept N.

Found by auditing the analytical surfaces with the question that fits them — not "does it refuse?" but
"does it report a number its input cannot support?"

TWO defects, one call:

1. THE KEEP-BUDGET SLICED A STALE POPULATION. `active` is captured at the top of consolidate(); the hub
   and state-toggle passes then RETIRE records; the budget below still sliced the original list. On 30
   records with 30 distinct keys whose texts were near-identical, the toggle pass retired 29 and the
   budget dropped 20 more from a population that no longer existed — leaving ZERO active records and a
   recall() that returned nothing. With genuinely distinct texts the same call is correct (30 -> 10),
   which is why it survived: it only shows once an earlier pass has fired.

2. `kept` WAS THE REQUEST, ECHOED. Literally `"kept": keep`. It sat in the same dict as `active`, so a
   run that left 0 active reported `kept: 10` and contradicted itself in one line. It is the surviving
   population now; the request is reported as `keep_requested`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

DISTINCT = [
    "the billing API authenticates callers with OAuth2 bearer tokens",
    "the staging database runs on host db-staging-07 in eu-west-1",
    "the deploy script targets the main branch by default since March",
    "the Pro pricing tier costs 39 dollars per month with annual billing",
    "the cache layer evicts entries using a two-tier value-protected policy",
    "sessions in the auth service expire after 15 minutes of inactivity",
    "the nightly report job runs at 02:00 UTC and pages on failure",
    "Maria is the technical lead of project Atlas since the reorg",
    "the search index is rebuilt weekly from the primary replica",
    "the payments gateway retries failed captures three times",
    "the mobile client caches avatars for seven days on device",
    "the ingest worker batches events in windows of five seconds",
    "the audit log is retained for ninety days then archived to cold storage",
    "feature flags roll out to five percent of traffic before a full release",
    "the CDN edge serves static assets from thirty-two points of presence",
    "the queue consumer acknowledges messages only after a durable write",
    "the metrics pipeline samples traces at one percent under load",
    "the backup vault keeps three generations of encrypted snapshots",
    "the tenant router shards customers by a hash of their account id",
    "the webhook relay signs every callback with an HMAC header",
]


def _store(texts):
    st = Inspeximus(path=None)
    for i, t in enumerate(texts):
        st.remember(t, key=f"k{i}", object=f"v{i}", source={"doc": f"team-{i % 3}"})
    return st


def _active(st):
    return len([r for r in st.items if r.get("status") == "active"])


def test_consolidation_never_empties_the_store():
    """THE defect. Near-identical texts made an earlier pass fire; the budget then over-dropped."""
    st = _store([f"the billing service fact number {i}" for i in range(30)])
    st.consolidate(keep=10)
    assert _active(st) > 0, "consolidate left the store with no active records"
    assert st.recall("billing service fact", k=5), "recall returns nothing after consolidation"


def test_the_keep_budget_is_not_applied_to_records_an_earlier_pass_retired():
    """The mechanism, asserted directly: nothing is staled that was already gone."""
    st = _store([f"the billing service fact number {i}" for i in range(30)])
    res = st.consolidate(keep=10)
    survivors = _active(st)
    assert res["staled"] <= max(0, survivors + res["staled"] - res["keep_requested"]) + 1, res
    assert res["active"] == survivors


def test_kept_reports_what_survived_not_what_was_asked_for():
    st = _store([f"the billing service fact number {i}" for i in range(30)])
    res = st.consolidate(keep=10)
    assert res["kept"] == _active(st), f"kept={res['kept']} but {_active(st)} records are active"
    assert res["keep_requested"] == 10
    # the two must never contradict each other inside one report
    assert res["kept"] == res["active"]


def test_the_ordinary_case_is_unchanged():
    """The control. On genuinely distinct facts the budget must still trim to exactly `keep`."""
    st = _store(DISTINCT)
    res = st.consolidate(keep=8)
    assert _active(st) == 8, res
    assert res["kept"] == 8 and res["keep_requested"] == 8
    assert res["staled"] == len(DISTINCT) - 8, res


def test_a_keep_larger_than_the_store_changes_nothing():
    st = _store(DISTINCT[:5])
    before = _active(st)
    res = st.consolidate(keep=100)
    assert _active(st) == before
    assert res["kept"] == before and res["staled"] == 0
