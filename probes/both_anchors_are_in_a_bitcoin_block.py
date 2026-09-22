#!/usr/bin/env python
"""Are our two published anchors actually in a Bitcoin block, and can the check say no?

A receipt that nobody has ever carried through to a block is a promise. This asks the calendars
which block covers each anchor, fetches that block's header from a public explorer, and verifies
the proof against it with the bundled verifier: no `ots` command, no node, no python-bitcoinlib.

    python probes/both_anchors_are_in_a_bitcoin_block.py --dir <the witness/ots directory>

Every anchor is checked THREE times: as published, with one byte of the head changed, and with one
byte of the block header changed. The last two must both fail. An anchor that verifies while its own
controls also verify has been checked by nothing.

Exit 0 when every anchor is ANCHORED and every control fails, 1 otherwise, 2 when the calendars or
the explorer could not be reached (which is not a verdict about any anchor).
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus.opentimestamps import block_hash_of, upgrade, verify   # noqa: E402

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                                    # noqa: BLE001
    CTX = ssl.create_default_context()

EXPLORER = "https://blockstream.info/api"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "inspeximus-anchor-probe/1"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return r.read().decode().strip()


def block_header(height: int) -> bytes:
    """The 80-byte header of a block, and a check that it IS that block before it is used."""
    block_hash = _get("%s/block-height/%d" % (EXPLORER, height))
    header = bytes.fromhex(_get("%s/block/%s/header" % (EXPLORER, block_hash)))
    if block_hash_of(header) != block_hash:
        raise RuntimeError("the explorer returned a header that does not hash to block %d" % height)
    return header


def check_one(head_path: str) -> dict:
    # The receipts are stamped over LF bytes in Linux CI. A copy checked out on Windows has CRLF and
    # is not the stamped file, so the comparison is made against the bytes that were stamped.
    data = open(head_path, "rb").read().replace(b"\r\n", b"\n")
    ots = open(head_path + ".ots", "rb").read()

    up = upgrade(ots)
    row = {"head": os.path.basename(head_path),
           "calendars": [{"calendar": c["calendar"],
                          "heights": [b["height"] for b in c.get("bitcoin", [])],
                          "error": c.get("error")} for c in up["calendars"]],
           "heights": up["heights"]}
    if not up["heights"]:
        row["verdict"] = "PENDING"
        row["why"] = "no calendar has a block for this digest yet"
        return row

    extra = [b for c in up["calendars"] for b in c.get("bitcoin", [])]
    height = up["heights"][0]
    header = block_header(height)

    out = verify(data, ots, block_header=header, extra_attestations=extra)
    row["verdict"] = out["verdict"]
    row["height"] = out.get("height")
    row["block_hash"] = out.get("block_hash")
    row["why"] = out["why"]

    # The controls. Each asserts it changed the bytes before believing the result, because a
    # corruption that changes nothing compares two identical inputs and reports that tampering is
    # fine.
    bad_data = bytearray(data)
    bad_data[len(bad_data) // 2] ^= 0x01
    assert bytes(bad_data) != data
    row["control_changed_head"] = verify(bytes(bad_data), ots, block_header=header,
                                         extra_attestations=extra)["verdict"]

    bad_header = bytearray(header)
    bad_header[40] ^= 0x01
    assert bytes(bad_header) != header
    row["control_changed_block"] = verify(data, ots, block_header=bytes(bad_header),
                                          extra_attestations=extra)["verdict"]
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="the directory holding index.json and the receipts")
    ap.add_argument("--out", default=os.path.join(HERE, "both_anchors_are_in_a_bitcoin_block.result.json"))
    a = ap.parse_args(argv)

    index = json.load(open(os.path.join(a.dir, "index.json"), encoding="utf-8"))
    rows = []
    try:
        for anchor in index["anchors"]:
            head = os.path.join(a.dir, anchor["receipt"][:-len(".ots")])
            row = check_one(head)
            row["log"] = anchor["log"]
            row["n_writes"] = anchor["n_writes"]
            rows.append(row)
    except Exception as exc:                                           # noqa: BLE001
        print("could not complete: %s: %s" % (type(exc).__name__, str(exc)[:200]))
        return 2

    receipt = {"kind": "inspeximus.anchor-probe/1",
               "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "anchors": rows,
               "scope": ("An anchored proof says these exact head bytes existed before that block "
                         "was mined. It says nothing about whether any entry is true, and it cannot "
                         "see a history shown to somebody else.")}
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)

    ok = True
    for row in rows:
        print("%-34s %-9s block %s" % (row["head"][:34], row["verdict"], row.get("height")))
        print("    %s" % row["why"])
        if row["verdict"] != "ANCHORED":
            ok = False
            continue
        for name in ("control_changed_head", "control_changed_block"):
            print("    %-24s %s" % (name, row[name]))
            if row[name] != "MISMATCH":
                print("    CONTROL FAILED: this check cannot say no, so its yes means nothing.")
                ok = False
    print("receipt: %s" % a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
