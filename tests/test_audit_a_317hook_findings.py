"""AUDIT-A's review of perf-317-hook (e9a98085), F-24 and F-25, taken in-tree by AUDIT-B, and the fast-exit comparison AUDIT-A
asked for. F-26 (a row with a malformed `meta` silences the prompt hook) predates the heal and is not part of this change.

F-24  the run's log is not opened beside the store, where a repository can ship a symlink at its name
F-25  a store that only the environment names is not healed
exit  the prompt hook run as a process leaves the same output and the same files with the fast exit as without it
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402

CHILD = """
import sys, unicodedata
unicodedata.unidata_version = '99.0.0'
import inspeximus.claude_code as cc
m = cc._store(sys.argv[1]); m._guard_key(create=True)
for i in range(6): m.remember("a note about the release, number %d" % i, key="n%d" % i)
m.flush()
"""
EVENT = {"hook_event_name": "UserPromptSubmit", "prompt": "what did we note about the release", "session_id": "t"}


def _env(tmp, extra=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    e.pop("PYTHONUNBUFFERED", None)
    e.update(INSPEXIMUS_KEY_HOME=os.path.join(tmp, "kh"), INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONPATH=ROOT,
             HOME=os.path.join(tmp, "home"), USERPROFILE=os.path.join(tmp, "home"))
    e.update(extra or {})
    return e


def _project(tmp, name):
    p = os.path.join(tmp, name)
    os.makedirs(os.path.join(p, ".git"))
    os.makedirs(os.path.join(tmp, "home"), exist_ok=True)
    r = subprocess.run([sys.executable, "-c", CHILD, p], env=_env(tmp), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-300:]
    return p


def _hook(tmp, cwd, extra=None):
    ev = dict(EVENT, cwd=cwd)
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev), cwd=tmp,
                          env=_env(tmp, extra), capture_output=True, text=True, encoding="utf-8", timeout=120)


def _foreign(tmp, cwd, extra=None):
    code = "import sys,inspeximus.claude_code as cc\nm=cc._store(sys.argv[1])\nprint(cc.foreign_stamp_count(m))"
    r = subprocess.run([sys.executable, "-c", code, cwd], env=_env(tmp, extra), capture_output=True, text=True, encoding="utf-8")
    return int(r.stdout.strip())


def _wait_healed(tmp, cwd, extra=None, secs=60):
    end = time.time() + secs
    while time.time() < end:
        if _foreign(tmp, cwd, extra) == 0:
            return True
        time.sleep(0.5)
    return False


# F-24 ------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="a symlink needs POSIX (or a privilege); the next test covers Windows")
def test_f24_a_symlinked_log_file_in_a_repository_is_not_followed(tmp_path):
    tmp = str(tmp_path)
    proj = _project(tmp, "repo")
    store = os.path.join(proj, ".inspeximus", "coding_memory.json")
    victim = os.path.join(tmp, "victim.txt")
    with open(victim, "w") as fh:
        fh.write("PRECIOUS USER FILE, written by the user, not by inspeximus\n")
    before = open(victim).read()
    os.symlink(victim, store + ".stamp-auto.log")
    os.symlink(victim, store + ".stamp-auto.json")
    _hook(tmp, proj)
    assert _wait_healed(tmp, proj), "the heal did not run"
    assert open(victim).read() == before, "the re-stamp overwrote the target of a symlink the repository shipped"


def test_f24_the_run_keeps_its_state_and_log_in_the_key_home_and_none_beside_the_store(tmp_path):
    tmp = str(tmp_path)
    proj = _project(tmp, "repo")
    store = os.path.join(proj, ".inspeximus", "coding_memory.json")
    before = set(os.listdir(os.path.dirname(store)))
    _hook(tmp, proj)
    assert _wait_healed(tmp, proj), "the heal did not run"
    time.sleep(0.5)
    new = set(os.listdir(os.path.dirname(store))) - before
    assert not [f for f in new if "stamp-auto" in f], new
    kdir = os.path.join(tmp, "kh", "inspeximus", "stamp-auto")
    names = os.listdir(kdir)
    assert any(n.endswith(".json") for n in names) and any(n.endswith(".log") for n in names), names


# F-25 ------------------------------------------------------------------------------------------------------------------

def test_f25_a_store_the_environment_names_is_not_rewritten_by_the_heal(tmp_path):
    tmp = str(tmp_path)
    other = _project(tmp, "otherproject")
    odir = os.path.join(other, ".inspeximus")
    repo = os.path.join(tmp, "cloned")
    os.makedirs(os.path.join(repo, ".git"))
    named = {"INSPEXIMUS_CODING_STORE": odir}
    assert _foreign(tmp, repo, named) == 6
    _hook(tmp, repo, named)
    time.sleep(8)
    assert _foreign(tmp, repo, named) == 6, "the heal re-stamped a store that only the environment named"
    assert not os.path.exists(os.path.join(tmp, "kh", "inspeximus", "stamp-auto")) or \
        not os.listdir(os.path.join(tmp, "kh", "inspeximus", "stamp-auto"))


def test_f25_the_run_refuses_a_store_the_environment_named(tmp_path):
    tmp = str(tmp_path)
    other = _project(tmp, "otherproject")
    odir = os.path.join(other, ".inspeximus")
    store = os.path.join(odir, "coding_memory.json")
    shim = ("import runpy,sys;sys.path[:]=[p for p in sys.path if p not in (str(),chr(46))];"
            "sys.path.insert(0,%r);runpy._run_module_as_main(sys.argv.pop(1))" % ROOT)
    r = subprocess.run([sys.executable, "-E", "-c", shim, "inspeximus.claude_code", "--stamp-guards", "--apply", "--auto",
                        "--expect-store", store], cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                       env=_env(tmp, {"INSPEXIMUS_CODING_STORE": odir}))
    assert "refused" in r.stdout, (r.stdout, r.stderr[-300:])
    assert _foreign(tmp, other) == 6


def test_f25_the_project_store_is_healed_when_nothing_in_the_environment_names_it(tmp_path):
    tmp = str(tmp_path)
    proj = _project(tmp, "repo")
    assert _foreign(tmp, proj) == 6
    _hook(tmp, proj)
    assert _wait_healed(tmp, proj), "the project's own store was not healed"


# exit ------------------------------------------------------------------------------------------------------------------

def _tree(root):
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.endswith(".lock") or ".tmp" in f:
                out[os.path.join(dp, f)] = "TEMPORARY"
            else:
                out[os.path.relpath(os.path.join(dp, f), root)] = os.path.getsize(os.path.join(dp, f)) > 0
    return out


@pytest.mark.parametrize("heal", [True, False])
def test_the_fast_exit_leaves_the_same_output_and_the_same_files(tmp_path, heal):
    runs = {}
    for fast in ("1", "0"):
        tmp = str(tmp_path / ("fast" + fast))
        os.makedirs(tmp)
        proj = _project(tmp, "repo")
        extra = {"INSPEXIMUS_HOOK_FAST_EXIT": fast}
        if not heal:
            extra["INSPEXIMUS_STAMP_AUTO"] = "0"
        r = _hook(tmp, proj, extra)
        assert r.returncode == 0, r.stderr[-300:]
        if heal:
            assert _wait_healed(tmp, proj, extra), "the heal did not finish"
        time.sleep(1.0)
        runs[fast] = (r.stdout, r.stderr, _tree(os.path.join(proj, ".inspeximus")), sorted(os.listdir(os.path.join(tmp, "kh", "inspeximus", "stamp-auto"))) if heal else [])
    out1, err1, tree1, kh1 = runs["1"]
    out0, err0, tree0, kh0 = runs["0"]
    assert json.loads(out1) == json.loads(out0), "the output differs"
    assert err1 == err0
    assert not [k for k, v in tree1.items() if v == "TEMPORARY"], tree1
    assert {k for k in tree1} == {k for k in tree0}
    assert len(kh1) == len(kh0)
