"""AUDIT-B 3.17.0: `core._stem` is memoised with a bounded `lru_cache`, and recall does not change.

A prompt hook calls `_stem` 511,051 times for 31,676 distinct words on our project store. The memo takes 0.12 s off a
1.6 s hook (9 interleaved runs, 2026-10-06). `_stem` is a module function, so no `_TenantView` rebinding is involved; the
tenant test below pins that a tenant view ranks as it did.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

WORDS = ["alice's", "agents'", "don't", "releases", "release", "class", "glass", "bus", "status", "boss", "xyz", "plans",
         "ab's", "cats", "dogs", "its", "analysis", "queries", "q" * 40 + "s"]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))


def test_the_memo_returns_what_the_function_returns():
    for w in WORDS:
        assert core._stem(w) == core._stem_uncached(w), w


def test_the_memo_is_bounded():
    info = core._stem.cache_info()
    assert info.maxsize == core._STEM_CACHE_SIZE == 65536
    core._stem.cache_clear()
    for i in range(core._STEM_CACHE_SIZE + 500):
        core._stem("word%dx" % i)
    assert core._stem.cache_info().currsize == core._STEM_CACHE_SIZE


def _build(tmp_path, tenant=None):
    base = Inspeximus(str(tmp_path / "s.json"))
    m = base.for_tenant(tenant) if tenant else base
    for i, t in enumerate(["Alice's phone is on the third floor", "the agents' release plans changed",
                           "a status report about releases and classes", "unrelated note about gardening bus routes",
                           "decision: the release process uses tags"] * 3):
        m.remember(t + " %d" % i, key="k%d" % i)
    return m


@pytest.mark.parametrize("tenant", [None, "acme"])
def test_recall_ranks_the_same_with_and_without_the_memo(tmp_path, monkeypatch, tenant):
    m = _build(tmp_path, tenant)
    q = "release plans for alice's status"
    cached = [(r["id"], r["text"]) for r in m.recall(q, k=8)]
    monkeypatch.setattr(core, "_stem", core._stem_uncached)
    plain = [(r["id"], r["text"]) for r in m.recall(q, k=8)]
    assert cached == plain and cached
