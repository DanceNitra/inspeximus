"""AUDIT-A's second review of 3.16.5 (d181563b): F-35 and F-36.

F-36  the isolation must not give up when a store holds many bad records
      a) numbers the arithmetic cannot use (inf, NaN, an integer too large for a float) are repaired at load, in every numeric field
      b) the search scans fixed chunks, is bounded by time and not by a count of reads, leaves out the chunks still failing when
         the time runs out and says so, never hides a record of a chunk that passed, and remembers what it left out
F-35  every link between the project root and the store file is judged, not only the last component
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bad_record_io import ROOT, QUERY, EVENT, _hand_edit, _project, _prompt, cc, sandbox  # noqa: E402,F401
from inspeximus import _isolate, _storelink, _surface  # noqa: E402

BAD_META = "x"          # meta.quarantined as a string: valid JSON, raises in the read, and not repaired at load


# ── F-36 b: many bad records ──────────────────────────────────────────────────────────────────────────────────────────

def _big_store(base, name, n, bad_every):
    """n records, every `bad_every`-th one hand-edited to a shape that raises in the read and is not repaired at load."""
    p = os.path.join(str(base), name)
    os.makedirs(os.path.join(p, ".git"))
    m = cc._store(p)
    m._guard_key(create=True)
    m.remember_decision("the release process uses the gate", because="it is the only check that runs", topic="release")
    for i in range(n):
        m.remember("release note number %d about the plan" % i, key="n%d" % i)
    m.flush()
    store = str(m.path)
    con = sqlite3.connect(store)
    bad_keys = set()
    rows = con.execute("SELECT id, doc FROM records").fetchall()
    for rid, doc in rows:
        d = json.loads(doc)
        k = d.get("key") or ""
        if k.startswith("n") and k[1:].isdigit() and int(k[1:]) % bad_every == 0:
            d.setdefault("meta", {})["quarantined"] = BAD_META
            con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d), rid))
            bad_keys.add(k[1:])
    con.commit()
    con.close()
    return p, store, bad_keys


def _served_keys(p, k):
    """The keys a read over the store returns, through the same isolation the hook uses."""
    m = cc._store(p)
    got = cc._read(m, lambda h: [r.get("text", "") for r in h.recall(QUERY, k=k)])
    return {t.split("number ")[1].split(" ")[0] for t in got if "release note number" in t}


def test_f36_when_the_time_runs_out_the_unexamined_chunks_are_left_out_and_counted(sandbox, monkeypatch):
    p, store, bad = _big_store(sandbox, "late", 300, 5)
    monkeypatch.setattr(_isolate, "TIME_S", 0.0)
    _isolate._SAID.clear()
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "failed" not in err, err[-200:]
    assert "limit ran out" in err and "not yet examined" in err, err
    assert "some of them are good" in err


def test_f36_a_chunk_that_passed_is_never_left_out(sandbox):
    """Every good record outside the chunks that held a bad record is served; bad records that are 100 apart sit in different
    chunks, so at most CHUNK - 1 good records could be lost per bad one, and the search finishes inside the time and loses none."""
    p, store, bad = _big_store(sandbox, "sparse", 600, 100)
    _isolate._SAID.clear()
    served = _served_keys(p, 700)
    good = {str(i) for i in range(600)} - bad
    assert good <= served, sorted(good - served)[:5]
    assert not (served & bad)


def test_f36_the_left_out_records_are_remembered_and_the_next_prompt_does_not_search_again(sandbox, monkeypatch):
    p, store, bad = _big_store(sandbox, "cache", 600, 10)
    calls = []
    real = _isolate._clone_over
    monkeypatch.setattr(_isolate, "_clone_over", lambda m, recs: calls.append(len(recs)) or real(m, recs))
    _isolate._SAID.clear()
    first_out, _ = _prompt(p)
    first = len(calls)
    calls.clear()
    _isolate._SAID.clear()
    second_out, _ = _prompt(p)
    second = len(calls)
    assert "release process uses the gate" in first_out and "release process uses the gate" in second_out
    assert first > 30 and second <= 6, (first, second)


def test_f36_a_record_that_changed_since_it_was_left_out_is_read_again(sandbox):
    p, store, bad = _big_store(sandbox, "changed", 300, 50)
    _isolate._SAID.clear()
    _prompt(p)                                                              # leaves the bad ones out and remembers them
    _hand_edit_all(store, lambda d: (d.get("meta") or {}).pop("quarantined", None))
    _isolate._SAID.clear()
    served = _served_keys(p, 400)
    assert bad <= served, "records that were repaired by hand must be served again: %s" % sorted(bad - served)[:3]


def _hand_edit_all(store_path, mutate):
    con = sqlite3.connect(store_path)
    try:
        for rid, doc in con.execute("SELECT id, doc FROM records").fetchall():
            d = json.loads(doc)
            mutate(d)
            con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d), rid))
        con.commit()
    finally:
        con.close()


def test_f36_a_failure_no_record_explains_is_still_raised_unchanged(sandbox):
    p, _ = _project(sandbox, "global")
    m = cc._store(p)

    def fn(h):
        raise RuntimeError("the embedder is down")
    with pytest.raises(RuntimeError, match="the embedder is down"):
        cc._read(m, fn)


# ── F-35: every component ─────────────────────────────────────────────────────────────────────────────────────────────

def _dir_link(link, target):
    os.makedirs(os.path.dirname(link), exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(link), os.path.normpath(target)], capture_output=True)
        else:
            pytest.skip("no link without a privilege")


def _shipped_dir_link(base):
    other = os.path.join(str(base), "other", ".inspeximus")
    os.makedirs(other)
    open(os.path.join(other, "memory.json"), "w").write("x")
    proj = os.path.join(str(base), "clone")
    os.makedirs(os.path.join(proj, ".git"))
    _dir_link(os.path.join(proj, ".inspeximus"), other)
    return proj, other


def test_f35_link_chain_sees_a_link_in_the_middle_of_the_path(sandbox):
    proj, other = _shipped_dir_link(sandbox)
    chain = _storelink.link_chain(os.path.join(proj, ".inspeximus"), "memory.json", proj)
    assert [os.path.basename(c) for c in chain] == [".inspeximus"], chain
    deeper = _storelink.link_chain(os.path.join(proj, ".inspeximus", "a", "b"), "memory.json", proj)
    assert [os.path.basename(c) for c in deeper] == [".inspeximus"], deeper


def test_f35_inspeximus_path_through_a_shipped_directory_link_is_refused_whatever_its_spelling(sandbox, monkeypatch):
    proj, other = _shipped_dir_link(sandbox)
    monkeypatch.chdir(proj)
    for spelling in (".inspeximus/memory.json", os.path.join(proj, ".inspeximus", "memory.json"),
                     "./.inspeximus/../.inspeximus/memory.json"):
        with pytest.raises(_surface.StoreLinkRefused):
            _surface.resolve_path(env={"INSPEXIMUS_PATH": spelling}, cwd=proj)


def test_f35_a_path_with_no_link_in_it_stays_as_it_is(sandbox, monkeypatch):
    proj = str(sandbox / "plain")
    os.makedirs(os.path.join(proj, ".git"))
    os.makedirs(os.path.join(proj, "data"))
    monkeypatch.chdir(proj)
    assert _surface.resolve_path(env={"INSPEXIMUS_PATH": "data/memory.json"}, cwd=proj) == "data/memory.json"


def test_f35_scope_project_and_the_environment_override_judge_the_same_chain(sandbox):
    proj, other = _shipped_dir_link(sandbox)
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.resolve_path(env={"INSPEXIMUS_SCOPE": "project"}, cwd=proj)
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.coding_store_path(proj, {"INSPEXIMUS_CODING_STORE": os.path.join(proj, ".inspeximus", "sub")})
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.coding_store_path(proj, {})


def test_f35_a_directory_link_in_the_middle_of_an_override_is_judged(sandbox):
    proj = str(sandbox / "proj")
    os.makedirs(os.path.join(proj, ".git"))
    outside = str(sandbox / "outside" / "store")
    os.makedirs(outside)
    _dir_link(os.path.join(proj, "mid"), str(sandbox / "outside"))
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.coding_store_path(proj, {"INSPEXIMUS_CODING_STORE": os.path.join(proj, "mid", "store")})
