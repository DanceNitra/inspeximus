"""A row store gives its free pages back, under the store lock, and never at a reader's expense (3.17).

Measured on a 13,489-record store: `compact_vectors` rewrote every vector as float16 and the file stayed at
254 MB, 140 MB of it free pages, because SQLite does not shrink a file by itself. `compact_vectors` and
`reembed` now end with a VACUUM, `inspeximus vacuum` runs one on demand, and `index_coherence` reports the
slack. A VACUUM needs the store lock and the file to itself; when either is not there within a short bound
it does nothing and reports the slack.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import sqlite_store as S  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIM = 1024                    # as bge-m3: a JSON list then spans overflow pages, which a shrink frees whole


def _emb(text):
    h = sum(map(ord, text)) % 251
    return [round(((h * (i + 7)) % 251) / 251.0 - 0.5, 7) for i in range(DIM)]


def _bloated(tmp_path, n=120):
    """A store whose vectors are JSON lists, as a release before 3.17 wrote them."""
    p = tmp_path / "m.json"
    m = Inspeximus(path=str(p), embed=_emb, persist_vectors=True)
    for i in range(n):
        m.remember("fact %d about the deploy window and the plan" % i, key="k%d" % i)
    m.flush()
    con = sqlite3.connect(str(p))
    rows = [(json.loads(d), rid) for rid, d in con.execute("SELECT id, doc FROM records")]
    for d, rid in rows:
        d.pop(S.VEC_KEY, None)
        d["vec"] = _emb(d["text"])
        con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d, sort_keys=True), rid))
    con.commit()
    con.execute("VACUUM")
    con.close()
    return p


def _open(p):
    return Inspeximus(path=str(p), embed=_emb, persist_vectors=True)


def test_compact_vectors_ends_with_a_vacuum_and_the_file_shrinks(tmp_path):
    p = _bloated(tmp_path)
    before = os.path.getsize(p)
    out = _open(p).compact_vectors()
    assert out["compacted"] == 120, out
    assert out["vacuum"]["vacuumed"] is True, out
    after = os.path.getsize(p)
    assert after < before * 0.6, (before, after)
    assert S.slack(p)["free_pages"] == 0
    assert all(r.get("vec") for r in _open(p).items), "a vector was lost in the rewrite"


def test_control_without_the_vacuum_the_file_keeps_its_size(tmp_path, monkeypatch):
    """CONTROL: the shrink above is the VACUUM's, not the compaction's."""
    p = _bloated(tmp_path)
    before = os.path.getsize(p)
    monkeypatch.setattr(Inspeximus, "vacuum", lambda self, wait_s=2.0: {"vacuumed": False, "reason": "test"})
    _open(p).compact_vectors()
    assert os.path.getsize(p) >= before * 0.95
    assert S.slack(p)["free_bytes"] > 0


def test_a_reader_holding_the_file_is_not_interrupted_and_the_slack_is_reported(tmp_path):
    p = _bloated(tmp_path)
    m = _open(p)
    reader = sqlite3.connect(str(p), isolation_level=None)
    reader.execute("BEGIN")
    first = reader.execute("SELECT count(*) FROM records").fetchone()[0]
    try:
        t = time.monotonic()
        out = m.vacuum(wait_s=0.3)
        assert time.monotonic() - t < 5.0, "the bound was not honoured"
        assert out["vacuumed"] is False and "locked" in out["reason"], out
        again = reader.execute("SELECT count(*) FROM records").fetchone()[0]
        assert again == first, "the reader's view changed under it"
    finally:
        reader.execute("COMMIT")
        reader.close()
    assert m.vacuum()["vacuumed"] is True, "control: with the reader gone the vacuum runs"


def test_another_writer_holding_the_store_lock_is_waited_for_only_within_the_bound(tmp_path):
    p = _bloated(tmp_path)
    m = _open(p)
    held, release = threading.Event(), threading.Event()

    def writer():
        with core._StoreLock(p):
            held.set()
            release.wait(10)
    th = threading.Thread(target=writer)
    th.start()
    held.wait(5)
    try:
        out = m.vacuum(wait_s=0.3)
        assert out["vacuumed"] is False and "store lock" in out["reason"] and "slack_bytes" in out, out
    finally:
        release.set()
        th.join(10)


def test_a_peer_handle_keeps_working_after_the_vacuum(tmp_path):
    p = _bloated(tmp_path)
    peer = _open(p)
    assert _open(p).compact_vectors()["vacuum"]["vacuumed"] is True
    peer.remember("written by a handle opened before the vacuum", key="peer")
    peer.flush()
    fresh = _open(p)
    keys = {r.get("key") for r in fresh.items if r.get("status") == "active"}
    assert "peer" in keys and len([k for k in keys if k and k.startswith("k")]) == 120, len(keys)
    ok, probs = Inspeximus(path=str(p), embed=_emb, persist_vectors=True).verify_writes()
    assert not [x for x in probs if "differs" in x or "malformed" in x], probs


def test_an_erasure_still_leaves_no_bytes_after_the_vacuum(tmp_path):
    p = _bloated(tmp_path)
    m = _open(p)
    m.compact_vectors()
    gone = next(r for r in m.items if r.get("key") == "k7")
    kept = next(r for r in m.items if r.get("key") == "k8")
    needles = [gone["text"].encode(), base64.b64encode(struct.pack("<%de" % DIM, *gone["vec"]))]
    control = base64.b64encode(struct.pack("<%de" % DIM, *kept["vec"]))
    assert all(n in open(p, "rb").read() for n in needles), "control: the record is on disk before the erasure"
    m.forget(gone["id"])
    m.flush()
    assert m.vacuum()["vacuumed"] is True
    data = b"".join(open(os.path.join(str(tmp_path), f), "rb").read() for f in os.listdir(str(tmp_path))
                    if os.path.isfile(os.path.join(str(tmp_path), f)))
    assert not any(n in data for n in needles), "the erased record is in a file after the vacuum"
    assert control in data, "control: a kept record's vector is still found"


def test_index_coherence_names_a_large_slack_and_not_a_small_one(tmp_path, monkeypatch):
    p = _bloated(tmp_path, n=250)                # over the 1 MiB floor below which slack is not a problem
    monkeypatch.setattr(Inspeximus, "vacuum", lambda self, wait_s=2.0: {"vacuumed": False, "reason": "test"})
    m = _open(p)
    m.compact_vectors()
    ic = m.index_coherence()
    assert ic["slack_bytes"] > 0 and any("free pages" in x for x in ic["problems"]), ic
    monkeypatch.undo()
    assert m.vacuum()["vacuumed"] is True
    ic = m.index_coherence()
    assert ic["slack_bytes"] == 0 and not any("free pages" in x for x in ic["problems"]), ic


def test_the_cli_vacuums_a_bloated_store(tmp_path):
    p = _bloated(tmp_path)
    con = sqlite3.connect(str(p))
    con.execute("DELETE FROM records WHERE rowid % 2 = 0")
    con.commit()
    con.close()
    assert S.slack(p)["free_bytes"] > 0, "CONTROL: the deletions left free pages"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_KEY_HOME=str(tmp_path / "kh"), INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(p), "vacuum"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0 and "freed" in r.stdout, (r.stdout, r.stderr[-500:])
    assert S.slack(p)["free_pages"] == 0


def test_reembed_ends_with_a_vacuum(tmp_path):
    p = _bloated(tmp_path)
    before = os.path.getsize(p)
    out = _open(p).reembed(only_missing=False)
    assert out["reembedded"] == 120 and out["vacuum"]["vacuumed"] is True, out
    assert os.path.getsize(p) < before * 0.6 and S.slack(p)["free_pages"] == 0
