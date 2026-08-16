"""safal207 named a third invalidation window. Do we cover it?

On anthropics/claude-code#34556 he generalised the two bugs (his in OmniMemory, ours in inspeximus)
into one temporal chain:

    OBSERVE -> BIND -> CAPTURE -> VERIFY -> USE

      mutation between OBSERVE  and CAPTURE  ->  UNBOUND_CAPTURE   (shipped, 2.10.6)
      mutation between CAPTURE  and VERIFY   ->  DRIFT             (shipped, long before)
      mutation between VERIFY   and USE      ->  TOCTOU / stale execution binding   <- ???

The first two we measure. The third we have never named, and a gap a collaborator names before we
do is the cheapest gap we will ever get. This measures whether ANY surface in inspeximus notices a
source that changed after a clean verification and before the memory was acted on.

WHAT WOULD REFUTE THE GAP: `verify_witness` going False on a source change, or a recall carrying a
freshness token that expires. Either would mean the window is already closed and there is nothing
to build.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

from inspeximus import Inspeximus

d = tempfile.mkdtemp()
src = os.path.join(d, "policy.txt")
blob = b"deployment needs two approvers"
open(src, "wb").write(blob)

ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
ix.remember("deployment needs two approvers", key="pol", object="two",
            source={"doc": src, "observed_sha256": hashlib.sha256(blob).hexdigest()})
ix.flush()

print("  T0  the agent observes, binds and captures")
v0 = ix.check_sources()
print(f"      check_sources           : {v0['counts']}  ok={v0['ok']}")
w0 = ix.witness()
print(f"      hydration witness       : digest {str(w0.get('digest'))[:16]}...")

print("\n  T1  VERIFY passes -- this is the moment an agent would act on the memory")
assert v0["counts"]["FRESH"] == 1 and v0["ok"], v0

print("\n  T2  the source changes AFTER the verification, BEFORE the use")
open(src, "wb").write(b"deployment needs ONE approver")

print("\n  T3  what, if anything, notices?")
r = ix.recall("approvers")
print(f"      recall still returns    : {r[0]['text']!r}")
vw = ix.verify_witness(w0)
print(f"      verify_witness          : digest_match={vw.get('digest_match')}  "
      f"(the STORE did not change, so this is CORRECT and also blind to the source)")
carries = [k for k in (r[0].get("meta") or {})
           if "fresh" in k or "verified" in k or "expires" in k or "valid_until" in k]
print(f"      freshness token on the record: {carries or 'none'}")
v1 = ix.check_sources()
print(f"      check_sources RE-RUN    : {v1['counts']}  ok={v1['ok']}")

print("\n" + "=" * 78)
gap = (vw.get("digest_match") is True and not carries)
if gap:
    print("  THE WINDOW IS OPEN. Nothing between a clean verification and the use of the memory")
    print("  reports that the ground moved. check_sources SEES it -- but only when something calls")
    print("  it again, and nothing in the recall path does. `verify_witness` is correct and blind:")
    print("  it pins the STORE's state, and the store did not change; the SOURCE did.")
    print("\n  So the honest statement for the thread: we measure two of the three windows, and the")
    print("  third is a re-run away from detectable but is not bound to the use.")
else:
    print("  NO GAP: something already covers VERIFY -> USE; do not claim otherwise.")
