"""Shared fixtures for the witness/audit-bundle tests.

`fork_of` lives here because three separate modules got it wrong in the same way, which makes it a
class of defect rather than three mistakes. Each of them built its "rewritten history" by creating a
SECOND store from scratch and handing the witness the victim's `store_id="prod"` label. That worked
only while the witness keyed its fork-memory on that caller-supplied label -- the very defect the
2.10.6 round fixed, because it let a rolled-back store be `cp`-ed elsewhere and re-witnessed as a
first contact. Once the witness keyed on the genesis receipt hash instead, those fixtures stopped
reaching the victim's history at all, and every one of them reported a pass.

A fork is a chain that SHARES A GENESIS and diverges after it. Two stores built independently are
two stores, and a witness reporting them as a fork of each other would be raising a false alarm.
"""
from __future__ import annotations

import json
import os
import shutil

from inspeximus import Inspeximus


def fork_of(ix, dest, records, receipt_key=None, keep=1):
    """A real fork of `ix` at `dest`: same genesis receipt, divergent history from `keep` onwards.

    `records` is a list of (text, key, object) written after the rollback. Returns the forked store,
    whose derived store id -- what the witness keys on -- equals the original's.
    """
    shutil.copytree(os.path.dirname(str(ix.path)), dest)
    p = os.path.join(dest, os.path.basename(str(ix.path)))
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    rows = rec if isinstance(rec, list) else rec["receipts"]
    kept = {r["memory_id"] for r in rows[:keep]}
    del rows[keep:]
    json.dump(rec, open(rp, "w", encoding="utf-8"))
    json.dump([r for r in json.load(open(p, encoding="utf-8")) if r["id"] in kept],
              open(p, "w", encoding="utf-8"))

    f = Inspeximus(path=p, receipts=True, receipt_key=receipt_key)
    for text, key, obj in records:
        f.remember(text, key=key, object=obj)
    f.flush()
    return f
