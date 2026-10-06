"""AUDIT-B 3.17.0 candidate, AUDIT-A's blocker and required change 4, in both directions, with the released code.

THE BLOCKER. An older writer (a pinned `uvx` MCP server that stays alive for days) reads `<store>.receipts.json` as
the whole chain. If the converted store kept an array there, the older writer would append to it and rewrite it,
and the tail's receipts would be gone from the chain it holds: a silent fork. The converted snapshot is therefore an
OBJECT. These tests run the released 3.16.1, 3.16.2 and 3.16.3 sources (`git archive`, one copy per tag) as
subprocesses on a converted store and check what AUDIT-A asked for:

  new writer, then old writer   the old writer fails on its write, neither receipt file changes by a byte, the chain
                                a new reader sees is the chain that was there, and the record the old writer saved
                                is named by `verify_writes` (loud, not silent)
  old writer, then new writer   an old writer stopped after its row and before its receipt, or in the middle of the
                                sidecar's replace, leaves a store the new writer converts without losing a receipt
                                and without hiding the record the old writer left uncovered
  there and back                `receipts to-legacy` gives the old writers their array again, and they extend it

The tests skip when `git` or a tag is missing (a shallow clone).
"""
import json
import os
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import receipts_tail as rt  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

TAGS = ["v3.16.1", "v3.16.2", "v3.16.3"]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    """{tag: directory holding that release's `inspeximus` package}, from `git archive`."""
    out = {}
    for tag in TAGS:
        z = tmp_path_factory.mktemp("frozen-" + tag.replace(".", "_")) / "src.zip"
        try:
            subprocess.run(["git", "-C", ROOT, "archive", "--format=zip", "-o", str(z), tag, "inspeximus"],
                           check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        with zipfile.ZipFile(z) as zf:
            zf.extractall(z.parent)
        out[tag] = str(z.parent)
    if not out:
        pytest.skip("git or the release tags are not available here")
    return out


def _run_old(root, body, store, extra_env=None):
    code = ("import sys, os\nsys.path.insert(0, %r)\nfrom inspeximus.core import Inspeximus\n"
            "import inspeximus\nP = %r\n" % (root, store)) + body
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_RECEIPTS_TAIL")}
    env.update(extra_env or {})
    env["PYTHONPATH"] = root
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       env=env, timeout=180)
    return r


OLD_WRITE = """
m = Inspeximus(P, receipts=True)
try:
    m.remember("written by the old writer", key="k-old")
    m.flush()
    print("WROTE")
except BaseException as e:
    print("REFUSED", type(e).__name__)
"""


def _bytes(p):
    out = {}
    for suffix in (".receipts.json", ".receipts.tail.jsonl"):
        try:
            out[suffix] = open(p + suffix, "rb").read()
        except FileNotFoundError:
            out[suffix] = None
    return out


def _new_store(tmp_path, n=6, tail=True):
    p = str(tmp_path / "s.json")
    mp = pytest.MonkeyPatch()
    try:
        if tail:
            mp.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
        m = Inspeximus(p, receipts=True)
        for i in range(n):
            m.remember(f"fact number {i}", key=f"k{i}")
        m.flush()
    finally:
        mp.undo()
    return p, m


def _pair(p):
    return rt.read(p + ".receipts.json", rt.tail_path(p + ".receipts.json"), core._GENESIS)


@pytest.mark.parametrize("tag", TAGS)
def test_an_older_writer_fails_on_a_converted_store_and_changes_neither_receipt_file(tmp_path, frozen, tag):
    if tag not in frozen:
        pytest.skip(f"{tag} is not in this clone")
    p, m = _new_store(tmp_path)
    before = _bytes(p)
    chain_before = [r["hash"] for r in _pair(p)["entries"]]
    r = _run_old(frozen[tag], OLD_WRITE, p)
    assert "REFUSED" in r.stdout and "WROTE" not in r.stdout, (r.stdout, r.stderr[-400:])
    assert _bytes(p) == before, "the older writer changed a receipt file: that is a fork"
    m2 = Inspeximus(p, receipts=True)
    assert [x["hash"] for x in m2._receipts] == chain_before, "a new reader sees the chain that was there"
    ok, problems = m2.verify_writes()
    assert not ok, "the row the older writer saved has no receipt, and verify_writes says so"
    assert any("k-old" in x or "written by the old writer" in x or "no write receipt" in x or "covered" in x
               for x in problems), problems


@pytest.mark.parametrize("tag", TAGS)
def test_an_older_reader_does_not_pass_a_converted_store(tmp_path, frozen, tag):
    if tag not in frozen:
        pytest.skip(f"{tag} is not in this clone")
    p, m = _new_store(tmp_path)
    r = _run_old(frozen[tag], "m = Inspeximus(P, receipts=True)\n"
                              "try:\n    ok, problems = m.verify_writes()\n    print('VERIFY', ok)\n"
                              "except BaseException as e:\n    print('RAISED', type(e).__name__)\n", p)
    assert "VERIFY True" not in r.stdout, "an older reader passed a chain it cannot read"


def test_the_new_writer_goes_on_after_an_older_writer_failed(tmp_path, frozen):
    tag = sorted(frozen)[0]
    p, m = _new_store(tmp_path)
    _run_old(frozen[tag], OLD_WRITE, p)
    m2 = Inspeximus(p, receipts=True)
    m2.remember("the new writer again", key="k-new")
    m2.flush()
    res = _pair(p)
    assert not res["problems"] and len(res["entries"]) == 7
    assert [e["seq"] for e in res["entries"]] == list(range(7))


STOP_AFTER_ROW = """
m = Inspeximus(P, receipts=True)
def stop(self, *a, **k):
    os._exit(9)
Inspeximus._emit_write_receipt = stop
m.remember("old writer, stopped before its receipt", key="k-stopped")
"""

STOP_IN_REPLACE = """
import inspeximus.core as core
m = Inspeximus(P, receipts=True)
real = core.os.replace
def replace(src, dst):
    if str(dst).endswith(".receipts.json"):
        os._exit(9)
    return real(src, dst)
core.os.replace = replace
m.remember("old writer, stopped in the sidecar replace", key="k-stopped")
"""


@pytest.mark.parametrize("stop", ["after_row", "in_replace"])
def test_an_old_writer_stopped_mid_write_leaves_a_store_the_new_writer_converts_without_loss(tmp_path, frozen,
                                                                                            monkeypatch, stop):
    tag = "v3.16.2" if "v3.16.2" in frozen else sorted(frozen)[0]
    p, m = _new_store(tmp_path, n=5, tail=False)
    assert isinstance(json.load(open(p + ".receipts.json")), list)
    chain_before = [r["hash"] for r in _pair(p)["entries"]]
    r = _run_old(frozen[tag], STOP_AFTER_ROW if stop == "after_row" else STOP_IN_REPLACE, p)
    assert r.returncode == 9, (r.returncode, r.stderr[-400:])
    # What the older release itself says of the files it left.
    old_view = _run_old(frozen[tag], "m = Inspeximus(P, receipts=True)\nok, pr = m.verify_writes()\n"
                                     "print('OK', ok, len(pr))\n", p).stdout
    assert "OK False" in old_view, old_view                  # a row without its receipt: named
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    m2 = Inspeximus(p, receipts=True)
    m2.remember("the new writer converts", key="k-new")
    m2.flush()
    res = _pair(p)
    assert res["mode"] == "tail" and not res["problems"]
    assert [e["hash"] for e in res["entries"]][:5] == chain_before, "no receipt was lost in the conversion"
    assert len(res["entries"]) == 6
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert not ok and any("k-stopped" in x or "old writer, stopped" in x or "no write receipt" in x or "covered" in x
                          for x in problems), "the row the older writer left without a receipt is still named"


@pytest.mark.parametrize("tag", TAGS)
def test_to_legacy_gives_older_writers_their_array_and_a_new_writer_converts_again(tmp_path, frozen, monkeypatch, tag):
    if tag not in frozen:
        pytest.skip(f"{tag} is not in this clone")
    p, m = _new_store(tmp_path)
    chain = [r["hash"] for r in _pair(p)["entries"]]
    out = rt.to_legacy(p)
    assert out["entries"] == 6 and not os.path.exists(p + ".receipts.tail.jsonl")
    assert isinstance(json.load(open(p + ".receipts.json")), list)
    r = _run_old(frozen[tag], OLD_WRITE, p)
    assert "WROTE" in r.stdout, (r.stdout, r.stderr[-400:])
    assert [x["hash"] for x in json.load(open(p + ".receipts.json"))][:6] == chain
    assert Inspeximus(p, receipts=True).verify_writes()[0]
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    m2 = Inspeximus(p, receipts=True)
    m2.remember("the new writer again", key="k-new")
    m2.flush()
    res = _pair(p)
    assert res["mode"] == "tail" and len(res["entries"]) == 8 and not res["problems"]
    assert Inspeximus(p, receipts=True).verify_writes()[0]
