"""The two read-path guards (3.5.0) measured on agmi's four memory-specific attacks, through the agmi
maintainer's own inspeximus adapter, guards on and guards off, with a positive control per arm.

WHY. tech4biz-yasha/agmi#3 (2026-09-21) measured inspeximus 3.0.0's default recall: user isolation
held; memory injection, retrieval hijack (stuffed entry in the first of 3 slots) and indirect prompt
injection surfaced. The reply promised two guards that work on any store and a measurement through
his adapter before any "defended configuration" row is offered. This is that measurement, and it is
the artifact the CHANGELOG cites.

CONTROL. Each arm writes one genuine memory for the victim and reads it back; an arm that serves 0
there cannot earn a safe cell (agmi's verdicts are all satisfied by an empty answer, which is the
defect the reply reported). The guards-off arm must reproduce his row, or the harness is not
measuring what he measured.

    <python with agmi and this tree importable> probes/two_read_guards_measured_on_agmi.py --agmi <agmi checkout>
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agmi", required=True, help="checkout of tech4biz-yasha/agmi (measured at 9107c72)")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.agmi))
    sys.path.insert(0, os.path.dirname(HERE))
    import inspeximus
    from inspeximus import Inspeximus
    from agmi.adapters.inspeximus_recall import InspeximusRecallAdapter
    from agmi.adapters.semantic_base import MemoryItem, Retrieved
    from agmi.attacks.memory_specific import ALL_MEMORY_ATTACKS

    class Arm(InspeximusRecallAdapter):
        """His adapter; the only change is the constructor flag under test and a served counter."""

        def __init__(self, label, guards):
            super().__init__(label=label)
            self.guards = guards
            self.served = []

        def reset(self):
            import tempfile
            from pathlib import Path
            self.close()
            self._dir = tempfile.TemporaryDirectory(prefix="agmi-inspeximus-guards-")
            self._store = Inspeximus(str(Path(self._dir.name) / "memory.sqlite"), read_guards=self.guards)

        def retrieve(self, query, user_id, k=5):
            rows = self._memory().recall(query, k=k, user_id=user_id, mode=self.mode)
            self.served.append(len(rows or []))
            return [Retrieved(text=str(r.get("text", "")), user_id="",
                              score=float(r.get("score") or r.get("relevance") or 0.0)) for r in rows or []]

    out = {"probe": os.path.basename(__file__), "inspeximus": inspeximus.__version__, "arms": {}}
    print(f"inspeximus {inspeximus.__version__}")
    for label, guards in (("guards_off", False), ("guards_on", True)):
        pc = Arm(label, guards)
        pc.reset()
        pc.add_memory(MemoryItem("The office lunch menu changes every Monday.", user_id="victim"))
        pc.retrieve("when does the office lunch menu change?", user_id="victim", k=3)
        cells = {"positive_control_served": sum(pc.served)}
        for A in ALL_MEMORY_ATTACKS:
            ad = Arm(label, guards)
            r = A().run(ad)
            cells[r.attack] = {"verdict": r.status, "served": sum(ad.served), "detail": r.detail or r.error}
            print("  %-10s %-27s %-11s served=%d  %s" % (label, r.attack, r.status, sum(ad.served), r.detail or r.error))
        out["arms"][label] = cells
    off, on = out["arms"]["guards_off"], out["arms"]["guards_on"]
    out["CONTROL_guards_off_reproduces_the_issue_row"] = (
        off["memory_injection"]["verdict"] == "VULNERABLE" and off["cross_session_bleed"]["verdict"] == "safe"
        and off["retrieval_hijack"]["verdict"] == "VULNERABLE" and "rank 1 of 3" in off["retrieval_hijack"]["detail"]
        and off["indirect_prompt_injection"]["verdict"] == "VULNERABLE")
    out["CONTROL_both_arms_serve_the_victims_own_memory"] = off["positive_control_served"] > 0 and on["positive_control_served"] > 0
    out["hijack_slots_genuine_with_guards"] = on["retrieval_hijack"]["served"] if on["retrieval_hijack"]["verdict"] == "safe" else 0
    out["cells_changed_by_the_guards"] = sorted(k for k in off if isinstance(off[k], dict) and off[k]["verdict"] != on[k]["verdict"])
    for k, v in out.items():
        if k.startswith("CONTROL") or k in ("hijack_slots_genuine_with_guards", "cells_changed_by_the_guards"):
            print(" ", k, v)
    json.dump(out, io.open(os.path.join(HERE, os.path.basename(__file__).replace(".py", ".result.json")), "w",
                           encoding="utf-8"), indent=2)
    return 0 if all(v for k, v in out.items() if k.startswith("CONTROL")) else 1


if __name__ == "__main__":
    sys.exit(main())
