"""The negative control for verify_ledger.py: every one-byte edit to the ledger must make it fail.

    python negative_control.py run            # exit 0 only if no single-byte edit verifies

A verifier that only ever says OK proves nothing, so this makes one edit at a time to a COPY of the
run and asks `verify_ledger.verify()` about each. Three kinds, at every byte position:

  substitute  the hardest single-byte change for that position rather than a random one: a space
              becomes a tab and a newline a space (the JSON still parses to the same entries), a hex
              digit a-f becomes upper case (the signature still decodes to the same bytes), a digit
              moves by one (in the last place of a float that can parse to the same number), anything
              else has its lowest bit flipped
  delete      the byte is removed
  insert      a space is inserted before it

The original run directory is never written. Exit 0 when the unmodified copy verifies and not one
edited copy does; exit 1 otherwise, listing the first edits that got through.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from verify_ledger import STORE_NAME, verify   # noqa: E402

KINDS = ("substitute", "delete", "insert")


def hardest_substitute(b: int) -> int:
    c = chr(b)
    if c == " ":
        return 0x09
    if c in "\n\t\r":
        return 0x20
    if "a" <= c <= "f":
        return ord(c.upper())
    if "0" <= c <= "9":
        return ord("0") + (int(c) + 1) % 10
    return b ^ 0x01


def edits(raw: bytes, kind: str):
    """Yield (position, description, edited bytes) for every position."""
    for i in range(len(raw)):
        if kind == "substitute":
            nb = hardest_substitute(raw[i])
            yield i, f"byte {i}: {raw[i:i + 1]!r} -> {bytes([nb])!r}", raw[:i] + bytes([nb]) + raw[i + 1:]
        elif kind == "delete":
            yield i, f"byte {i}: {raw[i:i + 1]!r} deleted", raw[:i] + raw[i + 1:]
        elif kind == "insert":
            yield i, f"byte {i}: space inserted before {raw[i:i + 1]!r}", raw[:i] + b" " + raw[i:]
        else:
            raise ValueError(f"unknown edit kind {kind!r}")


def sweep(run_dir, kinds=KINDS, **pins) -> dict:
    """Returns {"baseline_ok", "bytes", "edits": {kind: n}, "passed": [descriptions]}."""
    src = pathlib.Path(run_dir)
    with tempfile.TemporaryDirectory(prefix="crewai_negative_control_") as tmp:
        work = pathlib.Path(tmp) / "run"
        shutil.copytree(src, work)
        ledger = work / (STORE_NAME + ".actions.json")
        raw = ledger.read_bytes()
        out = {"baseline_ok": verify(work, **pins)["ok"], "bytes": len(raw), "edits": {}, "passed": []}
        for kind in kinds:
            n = 0
            for _i, desc, edited in edits(raw, kind):
                n += 1
                ledger.write_bytes(edited)
                if verify(work, **pins)["ok"]:
                    out["passed"].append(f"{kind} {desc}")
            out["edits"][kind] = n
        ledger.write_bytes(raw)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Every one-byte edit to the ledger must fail verification.")
    ap.add_argument("run_dir")
    ap.add_argument("--kind", action="append", choices=KINDS, help="repeatable; default: all three")
    a = ap.parse_args(argv)
    r = sweep(a.run_dir, kinds=tuple(a.kind or KINDS))
    total = sum(r["edits"].values())
    print(f"unmodified ledger verifies: {r['baseline_ok']}")
    for kind, n in r["edits"].items():
        print(f"  {kind:<10} {n} edits")
    for p in r["passed"][:20]:
        print("  PASSED (should have failed) " + p)
    ok = r["baseline_ok"] and not r["passed"]
    print(("OK " if ok else "FAIL ") + f"{total} one-byte edits over {r['bytes']} bytes, "
          f"{len(r['passed'])} verified")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
