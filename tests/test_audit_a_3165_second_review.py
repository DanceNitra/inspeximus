"""AUDIT-A's second review of 3.16.5 (d181563b): F-35 and F-36.

F-36  the isolation must not give up when a store holds many bad records
      a) numbers the arithmetic cannot use (inf, NaN, an integer too large for a float) are repaired at load, in every numeric field
      b) the search scans fixed chunks, is bounded by time and not by a count of reads, leaves out the chunks still failing when
         the time runs out and says so, and never hides a record of a chunk that passed
F-37  nothing is remembered between prompts, and a MemoryError is not proof that a record is bad
F-38  a `..` after a link is resolved as the kernel resolves it
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


# ── F-37 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_f37_nothing_is_remembered_between_prompts_and_a_starved_run_hides_nothing_for_good(sandbox, monkeypatch):
    p, store, bad = _big_store(sandbox, "starved", 300, 50)
    monkeypatch.setattr(_isolate, "TIME_S", 0.0)                            # a run whose time ran out: everything is left out
    _isolate._SAID.clear()
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "limit ran out" in err
    kh = os.path.join(str(sandbox), "kh")
    left = [f for dp, dn, fn in os.walk(kh) for f in fn if "left-out" in f]
    assert not left, "nothing may be written for a later prompt: %s" % left
    monkeypatch.setattr(_isolate, "TIME_S", 1.5)
    _isolate._SAID.clear()
    good = {str(i) for i in range(300)} - bad
    assert good <= _served_keys(p, 400), "a later normal prompt must serve every good record"
    assert not hasattr(_isolate, "_cache_put") and not hasattr(_isolate, "_from_cache")


def test_f37_a_memory_error_is_not_proof_that_a_record_is_bad(sandbox):
    p, _ = _project(sandbox, "memerr")
    m = cc._store(p)

    def fn(h):
        if len(h._items) == len(m._items):
            raise ValueError("the full read fails")
        raise MemoryError("the machine is out of memory")
    with pytest.raises(MemoryError):
        cc._read(m, fn)


# ── F-38 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _git_init(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, check=True)


def test_f38_dot_dot_after_a_shipped_link_is_refused_in_both_spellings(sandbox, monkeypatch):
    """`evil/../.inspeximus/memory.json`: the kernel goes up from the link's TARGET. The link is untracked in a real repository, so
    condition C would allow it, which is why a path that goes back up through a link is judged by A and B only."""
    other = str(sandbox / "other")
    os.makedirs(other + "/sub")
    os.makedirs(other + "/.inspeximus")
    open(other + "/.inspeximus/memory.json", "w").write("x")
    clone = str(sandbox / "clone")
    _git_init(clone)
    _dir_link(clone + "/evil", other + "/sub")
    monkeypatch.chdir(clone)
    assert _storelink.has_dotdot("evil/../.inspeximus/memory.json")
    assert [os.path.basename(x) for x in _storelink._physical_links("evil/../.inspeximus/memory.json", clone)] == ["evil"]
    for spelling in ("evil/../.inspeximus/memory.json", clone + "/evil/../.inspeximus/memory.json"):
        with pytest.raises(_surface.StoreLinkRefused):
            _surface.resolve_path(env={"INSPEXIMUS_PATH": spelling}, cwd=clone)


def test_f38_a_path_through_a_link_that_a_user_made_comes_back_as_the_real_path(sandbox, monkeypatch):
    """No `..`: an untracked link is the user's own (condition C), and the open takes the REAL path, never the link."""
    other = str(sandbox / "other" / ".inspeximus")
    os.makedirs(other)
    open(other + "/memory.json", "w").write("x")
    clone = str(sandbox / "clone")
    _git_init(clone)
    _dir_link(clone + "/.inspeximus", other)
    monkeypatch.chdir(clone)
    got = _surface.resolve_path(env={"INSPEXIMUS_PATH": ".inspeximus/memory.json"}, cwd=clone)
    assert got == os.path.realpath(other + "/memory.json"), got


@pytest.mark.skipif(os.name == "nt", reason="Windows collapses `..` before it follows a junction")
def test_f38_when_the_config_names_the_target_the_real_path_is_returned(sandbox, monkeypatch):
    other = str(sandbox / "other")
    os.makedirs(other + "/sub")
    os.makedirs(other + "/.inspeximus")
    open(other + "/.inspeximus/memory.json", "w").write("x")
    clone = str(sandbox / "clone")
    os.makedirs(clone + "/.git")
    os.symlink(other + "/sub", clone + "/evil")
    monkeypatch.chdir(clone)
    _storelink.add_configured_link(other + "/.inspeximus")
    got = _surface.resolve_path(env={"INSPEXIMUS_PATH": "evil/../.inspeximus/memory.json"}, cwd=clone)
    assert got == os.path.realpath(other + "/.inspeximus/memory.json"), got


@pytest.mark.skipif(os.name != "nt", reason="case differences matter on Windows")
def test_f38_a_path_spelled_in_another_case_is_judged_like_the_original_on_windows(sandbox, monkeypatch):
    other = str(sandbox / "other" / ".inspeximus")
    os.makedirs(other)
    proj = str(sandbox / "zip")
    os.makedirs(proj + "/.git")
    _dir_link(os.path.join(proj, ".inspeximus"), other)
    monkeypatch.chdir(proj)
    for spelling in (os.path.join(proj, ".inspeximus", "memory.json"), os.path.join(proj.upper(), ".INSPEXIMUS", "MEMORY.JSON"),
                     os.path.join(proj.lower(), ".Inspeximus", "memory.json")):
        with pytest.raises(_surface.StoreLinkRefused):
            _surface.resolve_path(env={"INSPEXIMUS_PATH": spelling}, cwd=proj)


# ── F-39 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_f39_components_split_on_this_systems_separators_only():
    assert _storelink._components("a/b//c") == ["a", "b", "c"]
    if os.name == "nt":
        assert _storelink._components("a\b/c") == ["a", "b", "c"]
    else:
        assert _storelink._components("a\b/c") == ["a\b", "c"], "a backslash is a file name character on POSIX"
        assert not _storelink.has_dotdot("a\..\b") and _storelink.has_dotdot("a\b/../c")


@pytest.mark.skipif(os.name == "nt", reason="a backslash is a separator on Windows; on POSIX git can ship a link named a\b")
def test_f39_a_link_whose_name_holds_a_backslash_is_one_component(sandbox, monkeypatch):
    other = str(sandbox / "other")
    os.makedirs(other + "/sub")
    os.makedirs(other + "/.inspeximus")
    open(other + "/.inspeximus/memory.json", "w").write("x")
    clone = str(sandbox / "clone")
    _git_init(clone)
    bs = chr(92)
    os.symlink(other + "/sub", clone + "/a" + bs + "b")
    monkeypatch.chdir(clone)
    spelling = "a" + bs + "b/../.inspeximus/memory.json"
    assert [os.path.basename(x) for x in _storelink._physical_links(spelling, clone)] == ["a" + bs + "b"]
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.resolve_path(env={"INSPEXIMUS_PATH": spelling}, cwd=clone)
