"""One bad record cannot silence the prompt hook (3.16.5, AUDIT-A F-29, the class behind F-26 and F-27).

A store a repository ships can carry a record that is valid JSON and wrong for one line of the read, rank or render path. Field
by field repair on load kept leaking (`meta.quarantined` as a string, `value` as a 400-digit integer). The class fix isolates the
record in the hook: a read that raises is repeated without the records that make it raise, with one stderr line, and the hook
answers. Nothing is written.

  * F-29 as AUDIT-A wrote it, through the hook as a process;
  * a fuzz: every top-level field and every meta field of a record, set by hand-editing the raw store file to wrong types,
    huge integers, inf, NaN, lone surrogates and deep nesting, each case a store of its own;
  * the isolation itself: a failure that no single record explains is raised unchanged, never swallowed.
"""
import json
import os
import subprocess
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bad_record_io import (EVENT, ROOT, _hand_edit, _project, _prompt, cc, sandbox)  # noqa: E402,F401
from inspeximus import _isolate  # noqa: E402


def test_f29_as_audit_a_wrote_it_through_the_hook_as_a_process(sandbox):
    bad = []
    for n, stmt in enumerate(("r['meta']['quarantined']='x'", "r['meta']['quarantined']=[1]", "r['value']=10**400")):
        p, store = _project(sandbox, "f29_%d" % n)
        code = ("import sys,inspeximus.claude_code as cc\nm=cc._store(sys.argv[1])\nr=[x for x in m.items if x['key']=='victim'][0]\n"
                "%s\nm._touched.add(r['id'])\nm.flush()" % stmt)
        env = {k: v for k, v in os.environ.items()}
        env["PYTHONPATH"] = ROOT
        assert subprocess.run([sys.executable, "-c", code, p], env=env, capture_output=True).returncode == 0
        r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(dict(EVENT, cwd=p)), cwd=str(sandbox),
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        if "failed" in r.stderr or not r.stdout.strip() or "release process uses the gate" not in r.stdout:
            bad.append((stmt, r.stderr.strip()[-120:], r.stdout[:80]))
    assert not bad, bad


def test_a_store_with_no_bad_record_never_imports_the_isolation(sandbox):
    p, _ = _project(sandbox, "clean")
    code = ("import sys, io\nimport inspeximus.claude_code as cc\nsys.stdin = io.StringIO(sys.argv[1])\ncc.main()\n"
            "sys.stderr.write('ISOLATE_IMPORTED=%s' % ('inspeximus._isolate' in sys.modules))")
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run([sys.executable, "-c", code, json.dumps(dict(EVENT, cwd=p))], env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert "release process uses the gate" in r.stdout, r.stderr[-300:]
    assert "ISOLATE_IMPORTED=False" in r.stderr, r.stderr[-300:]


def test_a_failure_no_single_record_explains_is_raised_unchanged(sandbox):
    """No blanket except: when the failure is not a record's, the isolation re-raises the original exception."""
    p, _ = _project(sandbox, "global")
    m = cc._store(p)

    def fn(h):
        raise RuntimeError("the embedder is down")
    with pytest.raises(RuntimeError, match="the embedder is down"):
        cc._read(m, fn)


def test_a_bad_record_is_left_out_of_the_answer_and_named_once(sandbox):
    p, store = _project(sandbox, "named")
    _hand_edit(store, lambda d: d.setdefault("meta", {}).__setitem__("quarantined", "x"))
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "number one" in out
    assert "another note about the release from the victim" not in out, "the bad record must be left out"
    assert err.count("left out of this answer") == 1 and "AttributeError" in err and "failed" not in err, err
    again_out, again_err = _prompt(p)                                   # the store is unchanged: still isolated, still said once per process
    assert "left out of this answer" not in again_err


def test_the_isolation_does_not_write_the_store(sandbox):
    p, store = _project(sandbox, "nowrite")
    _hand_edit(store, lambda d: d.setdefault("meta", {}).__setitem__("quarantined", "x"))
    import hashlib
    before = hashlib.sha256(open(store, "rb").read()).hexdigest()
    _prompt(p)
    assert hashlib.sha256(open(store, "rb").read()).hexdigest() == before


def test_two_bad_records_are_both_left_out(sandbox):
    p, store = _project(sandbox, "two")
    con = sqlite3.connect(store)
    for rid, doc in con.execute("SELECT id, doc FROM records").fetchall():
        d = json.loads(doc)
        if d.get("key") in ("victim", "n2"):
            d.setdefault("meta", {})["quarantined"] = "x"
            con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d), rid))
    con.commit()
    con.close()
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "number one" in out
    assert "number two" not in out and "victim" not in out
    assert "2 record(s)" in err, err


def test_a_record_that_raises_only_when_rendered_is_left_out_of_the_block(sandbox, monkeypatch):
    """The render path is isolated per record too: a record whose text breaks `_injected` is skipped, the others are shown."""
    p, _ = _project(sandbox, "render")
    real = cc._injected

    def picky(s, n=480):
        if "victim" in str(s):
            raise ValueError("cannot render")
        return real(s, n)
    monkeypatch.setattr(cc, "_injected", picky)
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "number one" in out
    assert "victim" not in out and "could not be rendered" in err, (out, err)


@pytest.mark.parametrize("bad_id", [[1, "a"], {"a": 1}, 7, 1.5, True])
def test_an_id_that_is_not_a_string_is_repaired_on_load_and_quarantined(sandbox, bad_id):
    """Opening a store keys a dict by id: a list or an object there raised TypeError at open, before any read could isolate it."""
    p, store = _project(sandbox, "badid")
    _hand_edit(store, lambda d: d.__setitem__("id", bad_id))
    m = cc._store(p)
    victim = [r for r in m.items if r.get("key") == "victim"][0]
    assert isinstance(victim["id"], str) and victim["id"].startswith("malformed-")
    assert victim["meta"]["malformed"]["id"] == bad_id
    assert victim["meta"]["quarantined"]["reason"] == "malformed_record"
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "failed" not in err
