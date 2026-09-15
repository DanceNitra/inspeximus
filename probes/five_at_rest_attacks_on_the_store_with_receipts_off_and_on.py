"""The five at-rest attacks of the agmi conformance suite, run against inspeximus in both configurations.

agmi (github.com/tech4biz-yasha/agmi, MIT, 2026-09) seeds a memory store through its own API, edits
the backing store behind its back, and asks the tool whether it noticed. Its published row for
LangGraph, Letta and Mem0 is "accepted" five times each. This probe runs the same five attacks
against inspeximus, with the attacker holding exactly what agmi's threat model gives them: write
access to the backing store (here a SQLite file with a `records(id, ord, doc)` table) and nothing
else. The tool's answer is `verify_writes()`, the same call `inspeximus audit-verify` and the
compliance overlay make; nothing here compares content and calls a difference "detected".

Two rows, because the honest answer has two halves. Receipts are OFF by default on a fresh store,
and a store with no chain cannot notice anything. That row does not read "accepted": verify_writes
refuses to vouch for ANY receipts-off store, touched or not ("receipts are DISABLED ... not the same
as verified"), so the outcome is scored "unverifiable", with the untouched store as the control that
gets the same answer. A fail-closed verifier is not detection. With receipts on and a signing key,
every attack must be refused for a reason that names it.

    python probes/five_at_rest_attacks_on_the_store_with_receipts_off_and_on.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from _receipt import write_receipt  # noqa: E402

from inspeximus import Inspeximus, new_receipt_keypair  # noqa: E402

ATTACKS = ("tamper", "truncate", "delete_middle", "reorder", "forge")


def _rows(path):
    c = sqlite3.connect(path)
    try:
        return [(i, o, json.loads(d)) for i, o, d in c.execute("select id, ord, doc from records order by ord")]
    finally:
        c.close()


def _attack(path, name):
    """Edit the backing store the way agmi's attacker does: raw SQL, valid encoding, nothing else."""
    rows = _rows(path)
    c = sqlite3.connect(path)
    try:
        if name == "tamper":
            rid, _o, doc = rows[len(rows) // 2]
            doc["text"] = doc["text"] + " (rewritten behind the tool's back)"
            c.execute("update records set doc=? where id=?", (json.dumps(doc), rid))
        elif name == "truncate":
            for rid, _o, _d in rows[-2:]:
                c.execute("delete from records where id=?", (rid,))
        elif name == "delete_middle":
            c.execute("delete from records where id=?", (rows[len(rows) // 2][0],))
        elif name == "reorder":
            (a, _oa, da), (b, _ob, db) = rows[1], rows[3]
            da2, db2 = dict(db), dict(da)
            da2["id"], db2["id"] = a, b                     # ids stay, contents swap: the tool sees valid rows
            c.execute("update records set doc=? where id=?", (json.dumps(da2), a))
            c.execute("update records set doc=? where id=?", (json.dumps(db2), b))
        elif name == "forge":
            tmpl = dict(rows[-1][2])
            tmpl["id"] = "f0r6ed00ff"
            tmpl["key"] = "planted"
            tmpl["text"] = "the customer agreed to the higher fee"
            c.execute("insert into records (id, ord, doc) values (?, ?, ?)", (tmpl["id"], rows[-1][1] + 1, json.dumps(tmpl)))
        c.commit()
    finally:
        c.close()


def _run(receipts: bool, work: str) -> dict:
    sk, pk = new_receipt_keypair() if receipts else (None, None)
    results = {}
    for name in ATTACKS:
        d = os.path.join(work, ("on-" if receipts else "off-") + name)
        os.makedirs(d)
        path = os.path.join(d, "memory.json")
        m = Inspeximus(path, receipts=receipts, receipt_key=sk)
        for i in range(6):                                   # seed through the tool's own API
            m.remember(f"fact {i}: the limit is {50 + i}", key=f"fact::{i}")
        del m
        _attack(path, name)
        m2 = Inspeximus(path, receipts=receipts, receipt_key=sk)   # reopen, the way a restart would
        ok, problems = m2.verify_writes(expected_pubkey=pk)
        loaded = len(list(m2.items))
        disabled = any("DISABLED" in p for p in problems)
        results[name] = {
            "tool_says_ok": bool(ok),
            "outcome": "accepted" if ok else ("unverifiable" if disabled else "detected"),
            "records_loaded": loaded,
            "first_problem": (problems[0][:160] if problems else None),
        }
    return results


def main() -> dict:
    work = tempfile.mkdtemp(prefix="inspeximus-agmi-")
    t0 = time.time()
    try:
        off = _run(False, work)
        on = _run(True, work)
        out = {
            "probe": os.path.basename(__file__),
            "inspeximus": __import__("inspeximus").__version__,
            "suite": "agmi at-rest attacks (tamper, truncate, delete_middle, reorder, forge), reproduced here, not the agmi harness itself",
            "threat_model": "write access to the backing SQLite file, nothing else; the tool's answer is verify_writes()",
            "receipts_off_default": off,
            "receipts_on_signed": on,
            "receipts_off_unverifiable": sum(1 for v in off.values() if v["outcome"] == "unverifiable"),
            "receipts_off_accepted": sum(1 for v in off.values() if v["outcome"] == "accepted"),
            "receipts_on_detected": sum(1 for v in on.values() if v["outcome"] == "detected"),
            "elapsed_s": round(time.time() - t0, 3),
        }
        # CONTROL: a store nobody touched verifies with receipts on
        d = os.path.join(work, "control")
        os.makedirs(d)
        sk, pk = new_receipt_keypair()
        m = Inspeximus(os.path.join(d, "memory.json"), receipts=True, receipt_key=sk)
        for i in range(6):
            m.remember(f"fact {i}", key=f"fact::{i}")
        out["control_untouched_store_verifies"] = Inspeximus(os.path.join(d, "memory.json"), receipts=True,
                                                             receipt_key=sk).verify_writes(expected_pubkey=pk)[0]
        # CONTROL: an untouched receipts-off store gets the same "unverifiable" answer as a tampered one,
        # which is why that row is not "detected"
        d2 = os.path.join(work, "control-off")
        os.makedirs(d2)
        m3 = Inspeximus(os.path.join(d2, "memory.json"))
        for i in range(6):
            m3.remember(f"fact {i}", key=f"fact::{i}")
        ok3, p3 = Inspeximus(os.path.join(d2, "memory.json")).verify_writes()
        out["control_untouched_receipts_off_is_unverifiable_too"] = (not ok3) and any("DISABLED" in p for p in p3)
        out["verdict"] = "PASS" if (out["receipts_on_detected"] == 5 and out["control_untouched_store_verifies"]
                                    and out["receipts_off_unverifiable"] == 5
                                    and out["control_untouched_receipts_off_is_unverifiable_too"]) else "FAIL"
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
    write_receipt(__file__, result)
    sys.exit(0 if result["verdict"] == "PASS" else 1)
