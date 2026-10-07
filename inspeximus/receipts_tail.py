"""The receipt chain as a snapshot plus an append-only tail (3.17.0 candidate, AUDIT-B prototype).

Why it exists. A receipted write rewrote the whole `<store>.receipts.json`: 16,252,227 bytes, one `os.replace` and
one fsync per `remember` on a copy of our MCP store. With a tail, a write appends one line of about 400 bytes and
fsyncs it; the snapshot is rewritten once per `COMPACT_AT` receipts.

Two files, one chain:

* `<store>.receipts.json`, the SNAPSHOT. In this format it is a JSON OBJECT, not an array:
  `{"kind": "inspeximus.receipts/2", "n": N, "tip": <hash of the last entry>, "entries": [...]}`.
  An object is the shape every released version refuses to extend: 3.16.1, 3.16.2 and 3.16.3 load it as the chain,
  fail on the first receipt they try to append (`KeyError: -1`) and leave the file as it was. A list that carried a
  marker entry was measured to let those versions append and rewrite the file, which forks the chain silently, so a
  marker inside an array is not used. The legacy array stays the format of a store nobody has converted.
* `<store>.receipts.tail.jsonl`, the TAIL. Line 1 is a header `{"kind": "inspeximus.receipts.tail/1",
  "base_pos": S, "base_hash": H}`: the tail continues a snapshot whose entry S-1 has hash H (the genesis hash when
  S is 0). Every other line is `{"pos": P, "entry": {...}}` with the receipt unchanged.

A reader takes the snapshot and the tail as one consistent pair and says what is wrong when they are not. The rules
are in `read`. The write side (lock, fsync order, outside head, compaction) is in `core.Inspeximus`, which owns the
chain in memory.
"""
from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path

SNAPSHOT_KIND = "inspeximus.receipts/2"
TAIL_KIND = "inspeximus.receipts.tail/1"
TAIL_SUFFIX = ".tail.jsonl"          #: appended to `<store>.receipts` to name the tail: `<store>.receipts.tail.jsonl`

#: Receipts the tail may hold before a write rewrites the snapshot. At 500 the amortised snapshot cost on a
#: 16 MB chain is about 32 KB per write, against 16 MB per write for the array.
COMPACT_AT = 500

#: `to_legacy` leaves this file beside the store. While it exists, the switch (`INSPEXIMUS_RECEIPTS_TAIL=1`) does not
#: convert the store again: a long-lived server that still has the switch on would otherwise undo the downgrade at its
#: next write. `to_tail` removes it.
MARKER_SUFFIX = ".receipts.legacy"


def marker_path(receipts_path) -> Path:
    """The downgrade marker beside a snapshot path (`<store>.receipts.json` -> `<store>.receipts.legacy`)."""
    p = str(receipts_path)
    return Path((p[:-len(".json")] if p.endswith(".json") else p) + ".legacy")


def marker_exists(receipts_path) -> bool:
    return marker_path(receipts_path).exists()


def tail_path(receipts_path) -> Path:
    """The tail beside a snapshot path (`<store>.receipts.json` -> `<store>.receipts.tail.jsonl`)."""
    p = str(receipts_path)
    stem = p[:-len(".json")] if p.endswith(".json") else p
    return Path(stem + TAIL_SUFFIX)


def snapshot_text(entries_json: str, n: int, tip: str) -> str:
    """The snapshot document, given the entries already encoded as one JSON array (`core._dump_chain_cached`)."""
    return ('{"kind": %s, "n": %d, "tip": %s, "entries": %s}'
            % (json.dumps(SNAPSHOT_KIND), n, json.dumps(tip), entries_json))


def header_line(base_pos: int, base_hash: str) -> bytes:
    return (json.dumps({"kind": TAIL_KIND, "base_pos": base_pos, "base_hash": base_hash}) + "\n").encode("utf-8")


def entry_line(pos: int, entry_json: str) -> bytes:
    """One tail line for a receipt already encoded by `core._encode_receipt`."""
    return ('{"pos": %d, "entry": %s}\n' % (pos, entry_json)).encode("utf-8")


def snapshot_sig(snapshot):
    """(mtime_ns, size) of the snapshot, or None when it is absent. The snapshot is only ever replaced whole."""
    try:
        st = os.stat(str(snapshot))
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read(snapshot, tail, genesis: str, known=None) -> dict:
    """Read the chain a snapshot and a tail hold.

    Returns {entries, mode, snap_n, disk_n, good_off, torn, problems, base_ok}:

    * `mode` is "list" for the legacy array (the tail is not read), "tail" for the snapshot object, and None when
      the snapshot is missing or is not JSON.
    * `entries` is every receipt that is part of the chain: the snapshot's, then the tail's that continue it. It stops
      at the first line that does not fit; `problems` names it. A damaged chain is never returned as a complete one.
    * `torn` is True when the file ends in bytes that are not a complete line: a write that was cut. It is
      information only; it is not in `problems`. `core.verify_writes` decides whether it is information, using the
      outside head (a head ahead of the last good line means a cut that the crash does not explain).
    * `good_off` is the byte offset in the tail after the last good line: where the next append goes.
    * `base_ok` is False when the header does not fit the snapshot, in which case a writer rewrites the snapshot
      rather than appending to a tail whose base it cannot trust.
    * `snap_sig` is the snapshot's stat taken before it was read.

    `known` is `(snap_sig, entries)` from an earlier read of the snapshot. When the snapshot's stat is still
    `snap_sig`, its entries are taken from `known` and only the tail is parsed: a peer's append then costs one
    small file, not the whole chain.
    """
    out = {"entries": [], "mode": None, "snap_n": 0, "disk_n": 0, "good_off": 0, "torn": False,
           "problems": [], "base_ok": True, "snap_sig": None}
    # THE TAIL IS READ BEFORE THE SNAPSHOT. A compaction replaces the snapshot first and the tail second, so a
    # reader that takes the tail first and the snapshot after can see an older tail with a newer snapshot (the
    # skip rule below reads that), and never a newer tail with an older snapshot, which would read as a snapshot
    # older than its tail.
    tail_data, tail_err = _read_bytes(tail)
    sig = snapshot_sig(snapshot)
    out["snap_sig"] = sig
    if known is not None and sig is not None and known[0] == sig:
        raw = {"kind": SNAPSHOT_KIND, "n": len(known[1]), "entries": list(known[1]),
               "tip": known[1][-1].get("hash") if known[1] else None}
    else:
        try:
            raw = json.loads(_read_bytes(snapshot, required=True)[0].decode("utf-8"))
        except FileNotFoundError:
            return out
        except Exception as e:                                 # noqa: BLE001
            out["problems"].append(f"{Path(snapshot).name} is not JSON ({type(e).__name__})")
            return out
    if isinstance(raw, list):
        out.update(entries=raw, mode="list", snap_n=len(raw), disk_n=len(raw))
        return out
    if not (isinstance(raw, dict) and raw.get("kind") == SNAPSHOT_KIND and isinstance(raw.get("entries"), list)):
        out["problems"].append(f"{Path(snapshot).name} is neither an array nor a {SNAPSHOT_KIND} object")
        return out
    entries = list(raw["entries"])
    snap_name = Path(snapshot).name
    out.update(mode="tail", entries=entries, snap_n=len(entries), disk_n=len(entries))
    if raw.get("n") != len(entries):
        out["problems"].append(f"{snap_name} says n={raw.get('n')} and holds {len(entries)} entries")
    if entries and raw.get("tip") != entries[-1].get("hash"):
        out["problems"].append(f"{snap_name} tip does not match its last entry")
    if tail_err is not None:
        out["problems"].append(f"{Path(tail).name} could not be read ({tail_err})")
        return out
    if tail_data is None:
        return out
    pieces = tail_data.split(b"\n")
    last = pieces.pop()                                        # b"" when the file ends in a newline
    out["torn"] = bool(last)
    off = 0
    snap_n = len(entries)
    tail_name = Path(tail).name
    for ln, piece in enumerate(pieces):
        end = off + len(piece) + 1
        try:
            doc = json.loads(piece.decode("utf-8"))
        except Exception:                                      # noqa: BLE001
            out["problems"].append(f"{tail_name} line {ln + 1} is not JSON")
            return out
        if ln == 0:
            if not (isinstance(doc, dict) and doc.get("kind") == TAIL_KIND
                    and isinstance(doc.get("base_pos"), int) and isinstance(doc.get("base_hash"), str)):
                out["problems"].append(f"{tail_name} has no header")
                out["base_ok"] = False
                return out
            bp, bh = doc["base_pos"], doc["base_hash"]
            if bp > snap_n:
                out["problems"].append(f"{tail_name} continues position {bp} and the snapshot holds only "
                                       f"{snap_n}: the snapshot is older than its tail")
                out["base_ok"] = False
                return out
            want = entries[bp - 1].get("hash") if bp > 0 else genesis
            if want != bh:
                out["problems"].append(f"{tail_name} base hash does not match the snapshot's entry {bp - 1}")
                out["base_ok"] = False
                return out
            off = end
            out["good_off"] = off
            continue
        if not (isinstance(doc, dict) and isinstance(doc.get("pos"), int) and isinstance(doc.get("entry"), dict)):
            out["problems"].append(f"{tail_name} line {ln + 1} is not a receipt line")
            return out
        pos, e = doc["pos"], doc["entry"]
        if pos < snap_n:
            # A compaction wrote the snapshot and was cut before it emptied the tail: this line is already in the
            # snapshot. It must be the same receipt.
            if entries[pos].get("hash") != e.get("hash"):
                out["problems"].append(f"{tail_name} line {ln + 1} (position {pos}) differs from the snapshot's entry")
                return out
        elif pos == len(entries):
            prev = entries[-1].get("hash") if entries else genesis
            if e.get("prev") != prev or e.get("seq") != pos:
                out["problems"].append(f"{tail_name} line {ln + 1} (position {pos}) does not link to the entry before it")
                return out
            entries.append(e)
        else:
            out["problems"].append(f"{tail_name} line {ln + 1} jumps to position {pos}, expected {len(entries)}")
            return out
        off = end
        out["good_off"] = off
    out["disk_n"] = len(entries)
    return out


def _read_bytes(path, required: bool = False, attempts: int = 40):
    """(bytes or None when the file does not exist, error name or None). Retries a PermissionError, which Windows
    raises while a writer's replace holds the name (`core._durable_replace` retries the other side)."""
    last = None
    for i in range(attempts):
        try:
            return Path(path).read_bytes(), None
        except FileNotFoundError:
            if required:
                raise
            return None, None
        except PermissionError as e:
            last = e
            time.sleep(0.005 * (i + 1))
        except OSError as e:
            return None, type(e).__name__
    return None, type(last).__name__


def read_entries(snapshot) -> list:
    """The chain as a list, for callers that only count or inspect it (the MCP server's start-up check)."""
    from .core import _GENESIS
    return read(snapshot, tail_path(snapshot), _GENESIS)["entries"]


def append(tail, data: bytes, truncate_to: "int | None" = None, new_file: bool = False) -> None:
    """Append `data` to the tail and fsync it. The file is opened and closed for each call, so no handle is held
    between writes: on Windows an open handle blocks a peer's compaction and a backup copy.

    `truncate_to` cuts a torn last line first (the offset `read` gave as `good_off`). `new_file` fsyncs the
    directory afterwards, so the file's existence survives a crash as well as its bytes."""
    from . import _safewrite
    if _safewrite.is_link(str(tail)):                  # 3.17.0: a link shipped at the tail's name is refused, as it is for every sidecar
        raise _safewrite.LinkRefused(errno.ELOOP, "inspeximus does not write through a link", str(tail))
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if truncate_to is None:
        flags |= os.O_APPEND
    fd = os.open(str(tail), flags, 0o666)
    try:
        if truncate_to is not None:
            os.ftruncate(fd, truncate_to)
            os.lseek(fd, 0, os.SEEK_END)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if new_file:
        fsync_dir(os.path.dirname(os.path.abspath(str(tail))) or ".")


def fsync_dir(d: str) -> None:
    """Best effort: POSIX can fsync a directory, Windows cannot open one this way."""
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _open(store_path):
    from .core import Inspeximus
    return Inspeximus(path=str(store_path), receipts=True)


def compact(store_path) -> dict:
    """Rewrite the snapshot to hold the whole chain and empty the tail. Returns {before, after, entries}."""
    from .core import _StoreLock
    m = _open(store_path)
    before = m._rc_snap_n
    with _StoreLock(m._receipts_path):
        m._reconcile_receipts_with_disk()
        if m._rc_mode != "tail":
            # Converting is the switch's job (INSPEXIMUS_RECEIPTS_TAIL=1), or `to_tail`'s. A compaction that converts
            # would leave a store that no released version can extend, without anyone having asked for that.
            raise ValueError("this store's receipt sidecar is the array, not the snapshot-plus-tail format, so there "
                             "is nothing to compact; `inspeximus receipts to-tail` converts it. Nothing was changed.")
        if m._rc_mode == "tail" and m._rc_problems:
            from .core import SidecarMalformed
            raise SidecarMalformed(list(m._rc_problems))
        m._compact_receipts_locked(m.__dict__.setdefault("_receipt_json", {}))
        m._receipts_sig = m._receipts_disk_sig()
    m._record_head()
    return {"snapshot_before": before, "snapshot_after": m._rc_snap_n, "entries": len(m._receipts)}


def to_legacy(store_path) -> dict:
    """Write the chain back as the array every released version reads, then remove the tail. A downgrade needs it.

    The array goes first and the tail is removed second: a cut between the two leaves a complete array and a tail
    nothing reads (`read` ignores the tail of an array store)."""
    from .core import SidecarMalformed, _StoreLock, _dump_chain, _durable_replace
    m = _open(store_path)
    path = m._receipts_path
    with _StoreLock(path):
        res = read(path, tail_path(path), __import__("inspeximus.core", fromlist=["_GENESIS"])._GENESIS)
        if res["mode"] == "tail" and res["problems"]:
            raise SidecarMalformed(list(res["problems"]), "nothing was changed")
        if res["mode"] == "tail":
            _durable_replace(path, _dump_chain(res["entries"]))
        _durable_replace(marker_path(path), json.dumps({"kind": "inspeximus.receipts.legacy/1", "at": time.time()}))
        try:
            os.remove(str(tail_path(path)))
        except FileNotFoundError:
            pass
    return {"mode_before": res["mode"], "entries": len(res["entries"])}


def to_tail(store_path) -> dict:
    """Convert an array store to the snapshot-plus-tail format and remove the downgrade marker. The explicit form of
    what `INSPEXIMUS_RECEIPTS_TAIL=1` does at the next receipted write."""
    from .core import SidecarMalformed, _StoreLock
    m = _open(store_path)
    path = m._receipts_path
    with _StoreLock(path):
        m._reconcile_receipts_with_disk()
        if m._rc_mode == "tail" and m._rc_problems:
            raise SidecarMalformed(list(m._rc_problems), "nothing was changed")
        try:
            os.remove(str(marker_path(path)))
        except FileNotFoundError:
            pass
        was = m._rc_mode
        if was != "tail":
            m._compact_receipts_locked(m.__dict__.setdefault("_receipt_json", {}))
            m._receipts_sig = m._receipts_disk_sig()
    m._record_head()
    return {"mode_before": was, "entries": len(m._receipts)}
