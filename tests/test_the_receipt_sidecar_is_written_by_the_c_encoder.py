"""The receipt sidecar is written without `indent`, and it is still the same evidence.

Every receipted write rewrites the whole sidecar. With `indent=2` that ran CPython's pure-Python
encoder over the whole chain: audits/2026-09-24/scale.md measured +165-184 ms per remember() at 10k
receipts and +870-900 ms at 50k. `_dump_chain` writes the same JSON array with the C encoder.

What must not move: the file is still what `json.loads` reads (older versions read it with nothing
else), a sidecar an older version wrote with `indent=2` still opens, verifies and extends, and a
receipt edited on disk is still caught by a fresh handle.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json.encoder

import inspeximus.core as core
from inspeximus import Inspeximus


def _store(n=6):
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=p, receipts=True)
    for i in range(n):
        m.remember("fact %d, with a non-ASCII name: Dráhoš" % i, key="k%d" % (i % 3), object="v%d" % i)
    return p, m


def _sidecar(p):
    return p + ".receipts.json"


def test_the_sidecar_holds_exactly_the_chain_in_memory():
    p, m = _store()
    disk = json.loads(open(_sidecar(p), encoding="utf-8").read())
    assert disk == m._receipts
    assert len(disk) == 6
    assert "Dráhoš" not in open(_sidecar(p), encoding="utf-8").read()   # receipts hold hashes, not text
    fresh = Inspeximus(path=p, receipts=True)
    assert fresh.verify_writes() == (True, [])


def test_a_sidecar_an_older_version_wrote_opens_verifies_and_extends():
    p, m = _store()
    old = json.dumps(m._receipts, indent=2, ensure_ascii=False)           # what <= 3.9.3 wrote
    with open(_sidecar(p), "w", encoding="utf-8") as fh:
        fh.write(old)
    again = Inspeximus(path=p, receipts=True)
    assert again.verify_writes() == (True, [])
    again.remember("one more", key="k9", object="v9")
    reread = json.loads(open(_sidecar(p), encoding="utf-8").read())      # how an older version reads it
    assert reread[:6] == json.loads(old) and len(reread) == 7
    assert Inspeximus(path=p, receipts=True).verify_writes() == (True, [])


def test_a_receipt_edited_on_disk_is_still_caught():
    p, m = _store()
    chain = json.loads(open(_sidecar(p), encoding="utf-8").read())
    chain[2]["commit"]["immutable_sha256"] = "0" * 64
    with open(_sidecar(p), "w", encoding="utf-8") as fh:
        fh.write(core._dump_chain(chain))
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok is False
    assert any(pr.startswith("receipt 2: receipt tampered") for pr in problems), problems


def test_a_record_edited_on_disk_is_still_caught_against_the_new_sidecar(monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")          # so the edit is a plain text edit
    p, m = _store()
    store = json.loads(open(p, encoding="utf-8").read())
    store[0]["text"] = "Revenue is 900M"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(store))
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok is False
    assert any("its TEXT or KEY no longer matches" in pr for pr in problems), problems


def test_a_receipted_write_does_not_run_the_pure_python_encoder(monkeypatch):
    """The cost itself, pinned without a clock: `indent` is what sends json.dumps to
    `_make_iterencode`. A receipted remember() must not call it."""
    p, m = _store(2)
    calls = []
    real = json.encoder._make_iterencode

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(json.encoder, "_make_iterencode", counting)
    m.remember("a receipted write", key="kz", object="vz")
    assert calls == [], "the receipt sidecar went back to the pure-Python encoder"
    assert json.loads(open(_sidecar(p), encoding="utf-8").read()) == m._receipts
