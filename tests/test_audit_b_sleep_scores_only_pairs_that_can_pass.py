"""AUDIT-B B-16: `sleep()` scores only the pairs and clusters that can pass, and returns what 3.15.1 did.

`consolidate_clusters` compared every later member of a ripe cluster with every earlier one: on a copy of
a 67k hook store that was 13,792,476 `_similarity` calls in one `sleep()`, of which 342,181 passed. The
overlap coefficient `|a & b| / min(|a|, |b|)` bounds which pairs can reach `dup_threshold`: the smaller
set must share at least `need` tokens, so any `|x| - need + 1` of its tokens contain one of the other
set's. The pair loop now visits only the members found that way, in the same order.

`_cluster_active` counts shared tokens per cluster. The count is the same; it is taken in C, by numpy
when it is installed and by `collections.Counter` otherwise.

Evidence:
  * a differential against the 3.15.1 implementations, frozen below: the clusters, the `sleep` report
    and every record's status, links and meta, on stores built to reach the boundaries (41 of 50 tokens
    at 0.82, 2 of 4 at 0.5, the smaller set later and earlier, empty token sets, value and negation
    toggles, a member superseded mid-loop), with and without numpy, and with an embedder;
  * the work counter: `_similarity` calls in the pair loop against the pairs the reference visits;
  * the mutants in tools/mutations.json that drop the prefix probe, shorten the prefix by one, and make
    the numpy comparison strict must each fail the differential.
"""
import json
import os
import shutil
import sys
import zlib

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

FROZEN_NOW = 1790000000.0


# ── the 3.15.1 reference, verbatim apart from the name and the module prefix ──────────────────────────

def ref_cluster_active(self, sim_threshold: float = 0.5):
    active = sorted([r for r in self.items if r["status"] == "active"
                     and (self.tenant is None or r.get("tenant") == self.tenant)],
                    key=lambda r: -r["value"])
    cents = []
    index = {}
    for r in active:
        rvec = self._qvec(r["text"])
        rtok = self._rec_tokens(r)
        best = None
        if self.embed and r.get("vec"):
            candidates = range(len(cents))
        else:
            shared = {}
            for t in rtok:
                for ci in index.get(t, ()):
                    shared[ci] = shared.get(ci, 0) + 1
            candidates = [ci for ci, n in shared.items()
                          if n >= sim_threshold * min(len(rtok), len(cents[ci]["tok"]))]
            candidates.sort()
        for ci in candidates:
            c = cents[ci]
            s = self._similarity(c["rec"]["text"], r, c["vec"], c["tok"])
            if s >= sim_threshold and (best is None or s > best[1]):
                best = (c, s)
        if best:
            best[0]["members"].append(r)
        else:
            cents.append({"rec": r, "vec": rvec, "tok": rtok, "members": [r]})
            for t in rtok:
                index.setdefault(t, []).append(len(cents) - 1)
    return [c["members"] for c in cents]


def ref_consolidate_clusters(self, threshold=15, cluster_sim=0.5, dup_threshold=0.82, keep_per_cluster=None):
    clusters = [[r for r in c if not Inspeximus._is_session_bookkeeping(r)]
                for c in self._cluster_active(cluster_sim)]
    fired = linked = toggled = staled = 0
    _live = [r for r in self.items if r.get("status") == "active"]
    _by_id = {r["id"]: r for r in self.items}
    for members in clusters:
        if len(members) < threshold:
            continue
        fired += 1
        members.sort(key=lambda r: -r["value"])
        for i, a in enumerate(members):
            if a["status"] != "active":
                continue
            avec = self._qvec(a["text"])
            atok = self._rec_tokens(a)
            for b in members[i + 1:]:
                if b["status"] != "active" or b["id"] in a["links"]:
                    continue
                if self._similarity(a["text"], b, avec, atok) >= dup_threshold:
                    if core._negation_clash(a["text"], b["text"]) or core._value_clash(a["text"], b["text"]):
                        _verdict, _older = self._resolve_state_toggle(a, b, _live, dup_threshold, _by_id)
                        if _verdict == "linked":
                            linked += 1
                            continue
                        toggled += 1
                        if _older is a:
                            break
                    else:
                        a["links"].append(b["id"]); linked += 1
        if keep_per_cluster is not None:
            act = sorted([r for r in members if r["status"] == "active"], key=lambda r: -r["value"])
            for r in act[keep_per_cluster:]:
                r["status"] = "superseded"
                self._touch(r); r["superseded_ts"] = core.time.time(); staled += 1
                r.setdefault("meta", {})["superseded_by_policy"] = "keep_budget"
                self._declare_retired(r, "keep-budget: outside the cluster's retained set")
    self._save(force=True)
    return {"clusters_total": len(clusters), "clusters_fired": fired, "threshold": threshold,
            "linked_pairs": linked, "toggled": toggled, "staled": staled}


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────

def _w(prefix, *ns):
    """A word of letters only. A digit inside a word is a number to `_value_clash`, which would turn
    every near-duplicate pair into a numeric update."""
    return prefix + "".join(chr(97 + n // 26) + chr(97 + n % 26) for n in ns)


def topic_texts(topics, per_topic):
    """Deterministic topics: 6 base words, one skewed shared word, one word unique to the record.

    Two members share 6 of 8 tokens (0.75, below 0.82) unless their skewed word matches (7 of 8, 0.875),
    so every topic is one ripe cluster with a known share of near-duplicates. Each topic also carries a
    numeric update and a negation, which toggle."""
    out = []
    for t in range(topics):
        base = " ".join(_w("tpc", t, j) for j in range(6))
        for i in range(per_topic):
            k = int((((i * 0.6180339887) % 1.0) ** 2) * 9)          # skewed: low k are common
            out.append(f"{base} {_w('skw', t, k)} {_w('own', t, i)}")
        out.append(f"{base} retry limit is 5")
        out.append(f"{base} retry limit is 9")
        out.append(f"{base} nightly cache enabled")
        out.append(f"{base} nightly cache not enabled")
    return out


def boundary_texts():
    """(text, value) pairs placed on the exact edges the filters must respect."""
    a = [_w("edg", j) for j in range(50)]
    rows = [
        (" ".join(a[:12]), 0.995),                                  # smaller set EARLIER than A
        (" ".join(a), 0.99),                                        # A, 50 tokens
        (" ".join(a[:41] + [_w("bxa", j) for j in range(9)]), 0.98),   # 41 of 50 = 0.82: passes
        (" ".join(a[:40] + [_w("bxb", j) for j in range(10)]), 0.97),  # 40 of 50 = 0.80: fails
        (" ".join(a[:10]), 0.30),                                   # smaller set LATER than A
    ]
    for f in range(16):                                             # fillers: the cluster is ripe
        o = f % 4                                                   # each shares >= 8 of the first 12
        rows.append((" ".join(a[o:o + 30] + [_w("fil", f, j) for j in range(5)]), 0.9 - f * 0.01))
    rows.append(("halfa halfb halfc halfd", 0.25))                   # a centroid of 4 tokens
    rows.append(("halfa halfb soloa solob", 0.20))                   # 2 of 4 = 0.5: joins it
    rows.append(("?? !! --", 0.15))                                  # no tokens at all
    rows.append(("of the and", 0.14))                                # stop words only: no tokens
    return rows


def _build(path, rows, embed=None):
    m = Inspeximus(path, embed=embed)
    for text, value in rows:
        m.remember(text, value=value)
    m.flush()


def _fake_embed(text):
    v = [0.0] * 16
    for w in core._tokens(text):
        v[zlib.crc32(w.encode()) % 16] += 1.0
    return v


_RUNS = [0]


def _run(src, tmp_path, name, reference, numpy_on, monkeypatch, embed=None):
    _RUNS[0] += 1
    p = str(tmp_path / f"{name}{_RUNS[0]}.json")                  # a fresh path: no sidecar carries over
    shutil.copy(src, p)
    with monkeypatch.context() as mp:
        mp.setattr(core.time, "time", lambda: FROZEN_NOW)
        if reference:
            mp.setattr(Inspeximus, "_cluster_active", ref_cluster_active)
            mp.setattr(Inspeximus, "consolidate_clusters", ref_consolidate_clusters)
        if not numpy_on:
            mp.setattr(core, "_numpy", lambda: None)
        m = Inspeximus(p, embed=embed)
        clusters = [[r["id"] for r in c] for c in m._cluster_active(0.5)]
        report = m.sleep()
        # 3.15.7 added `distinct_keys` to this report; the frozen 3.15.1 reference cannot carry it. The
        # differential compares everything else, and the fixtures hold no record whose key differs.
        assert report["consolidated_clusters"].pop("distinct_keys", 0) == 0
        state = sorted((r["id"], r.get("status"), sorted(r.get("links") or []),
                        json.dumps(r.get("meta") or {}, sort_keys=True, default=str)) for r in m._items)
    return clusters, report, state


def _numpy_modes():
    modes = [pytest.param(False, id="pure-python")]
    try:
        import numpy  # noqa: F401
        modes.append(pytest.param(True, id="numpy"))
    except ImportError:
        modes.append(pytest.param(True, id="numpy", marks=pytest.mark.skip(reason="numpy not installed")))
    return modes


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    d = tmp_path_factory.mktemp("b16")
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(core.time, "time", lambda: FROZEN_NOW)
        for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
            mp.delenv(k)
        out["topics"] = str(d / "topics.json")
        rows = topic_texts(12, 40)
        _build(out["topics"], [(t, round(1.0 - i / 4000, 6)) for i, t in enumerate(rows)])
        out["boundary"] = str(d / "boundary.json")
        _build(out["boundary"], boundary_texts())
        out["embedded"] = str(d / "embedded.json")
        _build(out["embedded"], [(t, round(1.0 - i / 4000, 6)) for i, t in enumerate(topic_texts(3, 30))],
               embed=_fake_embed)
    return out


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


# ── the differential ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("numpy_on", _numpy_modes())
@pytest.mark.parametrize("store", ["topics", "boundary"])
def test_sleep_returns_what_3_15_1_returned(stores, tmp_path, monkeypatch, store, numpy_on):
    ref = _run(stores[store], tmp_path, "ref", True, numpy_on, monkeypatch)
    new = _run(stores[store], tmp_path, "new", False, numpy_on, monkeypatch)
    assert new[0] == ref[0], "cluster membership or order differs from 3.15.1"
    assert new[1] == ref[1], "the sleep report differs from 3.15.1"
    assert new[2] == ref[2], "a record's status, links or meta differs from 3.15.1"


@pytest.mark.parametrize("numpy_on", _numpy_modes())
@pytest.mark.parametrize("threshold", [0.0, -1.0, 0.5, 1.0])
def test_clustering_matches_3_15_1_at_any_threshold(stores, tmp_path, monkeypatch, threshold, numpy_on):
    """The same clusters AND the same number of `_similarity` calls as 3.15.1. At a threshold of 0 or
    below every count passes the bound, and only the rule that a cluster must share a token keeps the
    candidates the same; a centroid that shares none scores 0 and never wins, so only the call count
    can tell."""
    out, calls = [], []
    real_sim = Inspeximus._similarity
    for reference in (True, False):
        p = str(tmp_path / f"t{int(reference)}.json")
        shutil.copy(stores["boundary"], p)
        n = [0]

        def sim(self, *a, **k):
            n[0] += 1
            return real_sim(self, *a, **k)

        with monkeypatch.context() as mp:
            mp.setattr(core.time, "time", lambda: FROZEN_NOW)
            if not numpy_on:
                mp.setattr(core, "_numpy", lambda: None)
            mp.setattr(Inspeximus, "_similarity", sim)
            m = Inspeximus(p)
            fn = ref_cluster_active if reference else Inspeximus._cluster_active
            out.append([[r["id"] for r in c] for c in fn(m, threshold)])
        calls.append(n[0])
    assert out[1] == out[0]
    assert calls[1] == calls[0], f"3.15.1 scored {calls[0]} centroids, this code {calls[1]}"


def test_an_embedder_store_takes_the_3_15_1_path(stores, tmp_path, monkeypatch):
    ref = _run(stores["embedded"], tmp_path, "ref", True, True, monkeypatch, embed=_fake_embed)
    new = _run(stores["embedded"], tmp_path, "new", False, True, monkeypatch, embed=_fake_embed)
    assert new == ref


def test_the_fixtures_reach_the_cases_they_exist_for(stores, tmp_path, monkeypatch):
    """A differential over a fixture that never toggles or never fires proves nothing about those paths."""
    for store in ("topics", "boundary"):
        _clusters, report, _state = _run(stores[store], tmp_path, "ref", True, False, monkeypatch)
        rep = report["consolidated_clusters"]
        assert rep["clusters_fired"] >= 1 and rep["linked_pairs"] >= 1, store
    rep = _run(stores["topics"], tmp_path, "ref", True, False, monkeypatch)[1]["consolidated_clusters"]
    assert rep["toggled"] >= 12, "every topic carries a numeric update and a negation"


# ── the work counter ─────────────────────────────────────────────────────────────────────────────────

def _pair_calls(src, tmp_path, monkeypatch, reference):
    p = str(tmp_path / ("cref.json" if reference else "cnew.json"))
    shutil.copy(src, p)
    calls = {"cluster": 0, "pairs": 0}
    phase = ["pairs"]
    real_sim = Inspeximus._similarity
    cluster_fn = ref_cluster_active if reference else Inspeximus._cluster_active

    def sim(self, *a, **k):
        calls[phase[0]] += 1
        return real_sim(self, *a, **k)

    def cluster(self, *a, **k):
        phase[0] = "cluster"
        try:
            return cluster_fn(self, *a, **k)
        finally:
            phase[0] = "pairs"

    with monkeypatch.context() as mp:
        mp.setattr(core.time, "time", lambda: FROZEN_NOW)
        mp.setattr(Inspeximus, "_similarity", sim)
        mp.setattr(Inspeximus, "_cluster_active", cluster)
        if reference:
            mp.setattr(Inspeximus, "consolidate_clusters", ref_consolidate_clusters)
        Inspeximus(p).sleep()
    return calls


def test_the_pair_loop_scores_only_pairs_that_can_pass(stores, tmp_path, monkeypatch):
    ref = _pair_calls(stores["topics"], tmp_path, monkeypatch, reference=True)
    new = _pair_calls(stores["topics"], tmp_path, monkeypatch, reference=False)
    assert new["cluster"] == ref["cluster"], "clustering scores the same candidates as 3.15.1"
    assert new["pairs"] * 4 <= ref["pairs"], (
        f"the pair loop scored {new['pairs']} pairs; 3.15.1 scored {ref['pairs']}, and the prefix filter "
        "leaves only pairs sharing a rare token")


# ── part 3: the contradiction checks read each text's features once ──────────────────────────────────

class _RegexCalls:
    """Count calls into a compiled pattern's `search`, `findall` and `sub`, through sys.setprofile, which
    reports every call into a C method."""

    NAMES = ("search", "findall", "sub")

    def __enter__(self):
        self.n = 0

        def prof(frame, event, arg):
            if event == "c_call" and getattr(arg, "__name__", "") in self.NAMES \
                    and isinstance(getattr(arg, "__self__", None), core.re.Pattern):
                self.n += 1

        sys.setprofile(prof)
        return self

    def __exit__(self, *exc):
        sys.setprofile(None)
        return False


def _pair_loop_regex_calls(src, tmp_path, monkeypatch):
    p = str(tmp_path / "rx.json")
    shutil.copy(src, p)
    with monkeypatch.context() as mp:
        mp.setattr(core.time, "time", lambda: FROZEN_NOW)
        m = Inspeximus(p)
        m._cluster_active(0.5)                  # tokenizes every record, so the count below is the clash checks
        with _RegexCalls() as rx:
            report = m.sleep()["consolidated_clusters"]
    return rx.n, report, len(m._items)


def test_the_contradiction_checks_read_each_text_once(stores, tmp_path, monkeypatch):
    n, report, records = _pair_loop_regex_calls(stores["topics"], tmp_path, monkeypatch)
    assert report["linked_pairs"] + report["toggled"] > records, "the fixture must match more pairs than texts"
    assert n <= 4 * records, (
        f"{n} regex calls for {report['linked_pairs'] + report['toggled']} matched pairs over {records} "
        "records: the negation flag, the numbers and the text without them are per-text features")


def test_a_replaced_clash_check_is_still_called(stores, tmp_path, monkeypatch):
    """`_negation_clash` invites an LLM judge in its place. A pass that memoises the default features must
    call a replacement for every matched pair, as 3.15.1 did."""
    seen = {"neg": 0, "val": 0}

    def neg(a, b):
        seen["neg"] += 1
        return False

    def val(a, b):
        seen["val"] += 1
        return False

    p = str(tmp_path / "judge.json")
    shutil.copy(stores["topics"], p)
    with monkeypatch.context() as mp:
        mp.setattr(core.time, "time", lambda: FROZEN_NOW)
        mp.setattr(core, "_negation_clash", neg)
        mp.setattr(core, "_value_clash", val)
        report = Inspeximus(p).sleep()["consolidated_clusters"]
    assert report["toggled"] == 0, "the replacement said no pair clashes"
    assert seen["neg"] == seen["val"] == report["linked_pairs"] > 0
