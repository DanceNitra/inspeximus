"""AUDIT-A 3.22 F-9: the prompt hook's per-session set is counted apart from the store.

3.22 writes `<key home>/inspeximus/seen/<session>.json` and lists that directory once per session. Both were
counted as store work (replace_store, dir_listings), and prompt_hook_n1200's baseline was raised to let them in.
A later change that rewrote or listed the store once per prompt would then have stayed inside the raised numbers.
They are now `replace_seen` and `seen_listings`, and prompt_hook_n1200 is back to replace_store 0, dir_listings 2.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location("perf_gate_for_seen", os.path.join(ROOT, "perf", "gate.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def test_a_seen_set_write_and_listing_are_not_store_work(gate, tmp_path):
    seen = tmp_path / "inspeximus" / "seen"
    seen.mkdir(parents=True)
    store = tmp_path / "store.json"
    with gate.Counters() as c:
        (seen / "a.tmp").write_text("{}", encoding="utf-8")
        os.replace(str(seen / "a.tmp"), str(seen / "a.json"))
        os.listdir(str(seen))
        list(os.scandir(str(seen)))
        store.with_suffix(".tmp").write_text("[]", encoding="utf-8")
        os.replace(str(store.with_suffix(".tmp")), str(store))      # control: a store write is still store work
        os.listdir(str(tmp_path))                                     # control: another directory is a listing
    d = c.as_dict()
    assert (d["replace_seen"], d["seen_listings"]) == (1, 2), d
    assert (d["replace_store"], d["dir_listings"]) == (1, 1), d


def test_a_directory_merely_named_seen_is_not_the_set(gate, tmp_path):
    other = tmp_path / "project" / "seen"
    other.mkdir(parents=True)
    with gate.Counters() as c:
        os.listdir(str(other))
    assert c.as_dict()["dir_listings"] == 1 and c.as_dict()["seen_listings"] == 0


def test_the_prompt_hook_baseline_holds_no_store_write():
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as fh:
        c = json.load(fh)["prompt_hook_n1200"]["counters"]
    assert c["replace_store"] == 0 and c["dir_listings"] == 2, c
    assert c["replace_seen"] == 1 and c["seen_listings"] == 1, c      # the set is measured, not dropped
