"""AUDIT-B 3.17.0 candidate: what a receipted write costs in the snapshot-plus-tail format, and where the format has to
be known to the rest of the library.

Work counted, not seconds: whole-sidecar replaces, bytes written to the receipt files, fsyncs, handles left open.
On a copy of our MCP store (16,053 receipts, 16,247,197 bytes of sidecar) one receipted `remember` replaced the
sidecar once and wrote 16,254,237 bytes through `_durable_replace`; in the tail format it replaces nothing and
appends 1,029 bytes (AUDIT-B, 2026-10-06, `audit_b_317_receipts_tail.md`).
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import receipts_tail as rt  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")


def _pair(p):
    return rt.read(p + ".receipts.json", rt.tail_path(p + ".receipts.json"), core._GENESIS)


class _Work:
    """Counts of what the receipt files cost, wrapped around the three places they are written."""

    def __init__(self, mp):
        self.replaces, self.bytes, self.appends, self.opens = 0, 0, 0, 0
        real_durable, real_append, real_open = core._durable_replace, rt.append, os.open

        def durable(path, payload, *a, **k):
            if str(path).endswith(".receipts.json"):
                self.replaces += 1
            if str(path).endswith((".receipts.json", ".receipts.tail.jsonl")):
                self.bytes += len(payload) if isinstance(payload, bytes) else len(payload.encode("utf-8"))
            return real_durable(path, payload, *a, **k)

        def append(tail, data, *a, **k):
            self.appends += 1
            self.bytes += len(data)
            return real_append(tail, data, *a, **k)

        mp.setattr(core, "_durable_replace", durable)
        mp.setattr(rt, "append", append)


def test_a_receipted_write_appends_one_line_and_replaces_nothing(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    for i in range(20):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    w = _Work(monkeypatch)
    m.remember("the measured write", key="k-measured")
    m.flush()
    assert (w.replaces, w.appends) == (0, 1), "the snapshot is not rewritten for one receipt"
    one_line = len(rt.entry_line(21, core._encode_receipt(m._receipts[-1])))
    assert w.bytes == one_line < 4096, "the bytes written are the one line"


def test_the_snapshot_is_rewritten_once_per_compact_at_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "COMPACT_AT", 10)
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    m.remember("conversion", key="k-first")
    w = _Work(monkeypatch)
    for i in range(35):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    assert w.replaces == 3, f"36 receipts with a trigger of 10: three compactions, got {w.replaces}"
    res = _pair(p)
    assert len(res["entries"]) == 36 and not res["problems"]
    assert res["snap_n"] == 31, "the last compaction was at 31 entries; five lines are in the tail since"


def test_no_handle_to_the_tail_is_left_open_between_appends(tmp_path):
    """On Windows an open handle stops a peer's compaction (the replace) and a backup copy. The tail is opened and
    closed for each append, so renaming it between two writes works."""
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    m.remember("one", key="k1")
    m.remember("two", key="k2")
    tp = p + ".receipts.tail.jsonl"
    os.replace(tp, tp + ".moved")
    os.replace(tp + ".moved", tp)
    m.remember("three", key="k3")
    m.flush()
    assert len(_pair(p)["entries"]) == 3


def test_the_tail_is_a_sidecar_to_every_list_that_names_them(tmp_path):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    alice = m.remember("Alice Example lives in Nitra", key="person:alice")
    m.remember("another fact", key="k2")
    m.flush()
    names = sorted(os.listdir(tmp_path))
    assert "s.json.receipts.tail.jsonl" in names
    sib = m._store_siblings()
    assert not sib["unknown"] and not sib["own"], f"the tail is a sidecar, not a copy of the records: {sib}"
    out = m.forget(ids=[alice], basis="test", request_id="r1")
    assert out["forgotten"] == 1 and out["tombstones"] == 1
    m.flush()
    cert = m.erasure_certificate(request_id="r1")
    assert not any("beside the store" in x for x in (cert.get("problems") or [])), cert.get("problems")
    ok, problems = m.verify_writes()
    assert ok, problems


def test_the_signing_key_a_receipt_chain_names_is_found_through_the_pair(tmp_path):
    pytest.importorskip("cryptography")
    from inspeximus.core import new_receipt_keypair
    sk, pk = new_receipt_keypair()
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipt_key=sk)
    for i in range(3):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    assert rt.read_entries(p + ".receipts.json")[-1]["pubkey"] == pk
    try:
        from inspeximus import mcp_server
    except Exception:                                              # noqa: BLE001 - the MCP extra is not installed
        pytest.skip("the MCP server module is not importable here")
    n, signers = mcp_server._chain_on_disk(p)
    assert n >= 3 and signers == {pk}, "the server's start-up check read the receipts through the pair"


def test_a_signed_chain_in_the_tail_verifies_and_a_swapped_signature_does_not(tmp_path):
    pytest.importorskip("cryptography")
    from inspeximus.core import new_receipt_keypair
    sk, pk = new_receipt_keypair()
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipt_key=sk)
    for i in range(4):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    assert Inspeximus(p, receipt_key=sk).verify_writes(expected_pubkey=pk)[0]
    tp = p + ".receipts.tail.jsonl"
    lines = open(tp, "rb").read().split(b"\n")
    doc = json.loads(lines[2])
    doc["entry"]["sig"] = "00" * 64
    lines[2] = json.dumps(doc).encode()
    open(tp, "wb").write(b"\n".join(lines))
    ok, problems = Inspeximus(p, receipt_key=sk).verify_writes(expected_pubkey=pk)
    assert not ok and problems


CONCURRENT = """
import sys
from inspeximus.core import Inspeximus
P, wid, per = sys.argv[1], sys.argv[2], int(sys.argv[3])
m = Inspeximus(P, receipts=True)
for i in range(per):
    for attempt in range(6):
        try:
            m.remember("worker %s write %d" % (wid, i), key="w%s-%d" % (wid, i))
            m.flush()
            break
        except Exception:
            m.reload()
"""


def test_concurrent_writers_lose_no_receipt_across_a_compaction(tmp_path, monkeypatch):
    """Six processes, 12 writes each, a trigger of 20 so a compaction lands among the appends. The array format
    replaced the file from each handle's copy; here a peer's lines are adopted under the lock, and the writer's own
    receipt follows them."""
    p = str(tmp_path / "s.json")
    seed = Inspeximus(p, receipts=True)
    seed.remember("seed", key="seed")
    seed.flush()
    env = dict(os.environ, PYTHONPATH=ROOT, INSPEXIMUS_RECEIPTS_TAIL="1", INSPEXIMUS_NO_UPDATE_CHECK="1")
    code = "import sys; sys.modules['inspeximus.receipts_tail'] = __import__('inspeximus.receipts_tail', fromlist=['x']); " \
           "import inspeximus.receipts_tail as r; r.COMPACT_AT = 20\n" + CONCURRENT
    procs = [subprocess.Popen([sys.executable, "-c", code, p, str(w), "12"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for w in range(6)]
    for pr in procs:
        out, err = pr.communicate(timeout=300)
        assert pr.returncode == 0, err[-600:]
    m = Inspeximus(p, receipts=True)
    ids = {r["id"] for r in m.items}
    covered = {r["memory_id"] for r in m._receipts}
    assert len(ids) == 73 and ids == covered, (len(ids), len(covered), len(ids - covered), len(covered - ids))
    assert [r["seq"] for r in m._receipts] == list(range(len(m._receipts)))
    ok, problems = m.verify_writes()
    assert ok, problems
    assert _pair(p)["snap_n"] > 1, "a compaction happened among the appends"


def test_a_batch_as_long_as_the_tail_limit_is_one_snapshot_write_and_no_tail_lines(tmp_path, monkeypatch):
    """`recommit` and the backfill emit many receipts and write them once. At the tail limit that is one snapshot
    write, not an append that the next write would compact. The store is already in the tail format here, so the
    conversion write does not stand in for it."""
    monkeypatch.setattr(rt, "COMPACT_AT", 5)
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    m.remember("converts the store", key="k-first")
    assert _pair(p)["mode"] == "tail"
    m.receipts_enabled = False                                  # eight records with no receipt
    ids = [m.remember(f"fact number {i}", key=f"k{i}") for i in range(8)]
    m.receipts_enabled = True
    m.flush()
    w = _Work(monkeypatch)
    m._defer_receipt_flush = True
    for rec in [r for r in m._items if r["id"] in ids]:
        m._emit_write_receipt(rec)
    m._defer_receipt_flush = False
    m._persist_receipts()
    assert w.replaces == 1 and w.appends == 0, (w.replaces, w.appends)
    res = _pair(p)
    assert res["snap_n"] == 9 == len(res["entries"]) and not res["problems"]


def test_a_reader_that_meets_a_compaction_between_its_two_reads_finds_no_problem(tmp_path, monkeypatch):
    """The compaction replaces the snapshot, then the tail. The tail is read first, so a compaction that lands
    between the two reads gives an older tail with a newer snapshot, which reads as consistent."""
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    for i in range(5):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    assert _pair(p)["snap_n"] == 1
    real, fired = rt._read_bytes, []

    def compact_after_the_first_read(path, **k):
        out = real(path, **k)
        if not fired:
            fired.append(1)
            monkeypatch.setattr(rt, "_read_bytes", real)
            rt.compact(p)
        return out

    monkeypatch.setattr(rt, "_read_bytes", compact_after_the_first_read)
    res = rt.read(p + ".receipts.json", rt.tail_path(p + ".receipts.json"), core._GENESIS)
    assert fired and not res["problems"] and len(res["entries"]) == 5, res["problems"]
