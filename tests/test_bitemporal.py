"""1.5.0: bitemporal query — as_of(key, when, as_recorded) + believed_at. The second clock (transaction-time)
lets you reconstruct 'what did we believe at tx-time T' without a later correction leaking in."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo import Mnemo


def _store_with_correction():
    m = Mnemo()
    a = m.remember("region is us-east", key="db::region", valid_from=100.0)
    b = m.remember("region is eu-west", key="db::region", valid_from=200.0)   # a genuine correction
    by = {r["id"]: r for r in m.items}
    by[a]["ts"] = 1000.0            # first fact recorded at tx-time 1000
    by[b]["ts"] = 2000.0            # correction recorded LATER, at tx-time 2000
    return m


def test_valid_time_still_works_default():
    m = _store_with_correction()
    # as_recorded=None -> legacy valid-time query: current value at when=250 is the correction
    assert "eu-west" in m.as_of("db::region", when=250.0)["text"]


def test_bitemporal_before_correction_recorded():
    m = _store_with_correction()
    # frozen at tx-time 1500 (BEFORE the correction was written) we believed us-east was current at when=250
    r = m.as_of("db::region", when=250.0, as_recorded=1500.0)
    assert "us-east" in r["text"] and r["as_recorded"] == 1500.0
    assert r["invalidated_at"] is None          # as known then, us-east was not yet superseded


def test_bitemporal_after_correction_recorded():
    m = _store_with_correction()
    r = m.as_of("db::region", when=250.0, as_recorded=2500.0)
    assert "eu-west" in r["text"]               # after the correction was recorded, we believe eu-west


def test_bitemporal_valid_time_still_respected():
    m = _store_with_correction()
    # even with full knowledge (as_recorded=2500), at world-time when=150 the value in effect was us-east
    r = m.as_of("db::region", when=150.0, as_recorded=2500.0)
    assert "us-east" in r["text"]


def test_believed_at_reconstructs_agent_belief():
    m = _store_with_correction()
    assert "us-east" in m.believed_at("db::region", 1500.0)["text"]   # what the agent believed at tx 1500
    assert "eu-west" in m.believed_at("db::region", 2500.0)["text"]   # ... and at tx 2500
    assert m.believed_at("db::region", 500.0) is None                # nothing recorded that early


def test_as_of_none_before_anything_known():
    m = _store_with_correction()
    assert m.as_of("db::region", when=50.0) is None                  # before any valid_from
