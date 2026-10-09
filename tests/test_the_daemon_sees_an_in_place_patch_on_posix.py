"""AUDIT-B R-1: an in-place, same-length patch of the store file with its modification time put back left every part of
the daemon's store signature as it was, so a held handle answered from the old bytes. On POSIX the signature carries
st_ctime_ns, which any write moves and no tool can set, and st_ino, which a replace moves."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import hookd  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _store(tmp_path):
    p = str(tmp_path / "s.db")
    m = Inspeximus(path=p)
    m.remember("the deploy window is friday")
    m.flush()
    return p


def _patch_in_place(p, old, new):
    st = os.stat(p)
    with open(p, "r+b") as fh:
        raw = fh.read()
        assert raw.count(old) >= 1 and len(old) == len(new), "CONTROL: the patch finds its bytes"
        fh.seek(raw.index(old))
        fh.write(new)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = os.stat(p)
    assert (after.st_mtime_ns, after.st_size) == (st.st_mtime_ns, st.st_size), "CONTROL: mtime and size restored"


def test_the_signature_carries_ctime_and_inode_on_posix(monkeypatch, tmp_path):
    """Platform-independent: the same mtime and size with another ctime or inode is another signature on POSIX."""
    p = _store(tmp_path)
    real = os.stat
    fake = {"ctime": 1, "ino": 7}

    def stat(f, *a, **k):
        st = real(f, *a, **k)
        if os.path.abspath(f) != os.path.abspath(p):
            return st
        return SimpleNamespace(st_mtime_ns=10, st_size=st.st_size, st_ctime_ns=fake["ctime"], st_ino=fake["ino"])
    monkeypatch.setattr(hookd.os, "stat", stat)
    monkeypatch.setattr(hookd.os, "name", "posix")
    base = hookd.store_signature(p)
    assert hookd.store_signature(p) == base, "CONTROL: an unchanged file keeps its signature"
    fake["ctime"] = 2
    assert hookd.store_signature(p) != base, "a write that restored the mtime is not seen"
    fake["ctime"], fake["ino"] = 1, 8
    assert hookd.store_signature(p) != base, "a replace that kept the mtime and size is not seen"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no inode change time: the stated 3.18 limit")
def test_a_real_in_place_patch_moves_the_signature(tmp_path):
    p = _store(tmp_path)
    base = hookd.store_signature(p)
    _patch_in_place(p, b"friday", b"monday")
    assert hookd.store_signature(p) != base, "the daemon would answer from the old bytes"
