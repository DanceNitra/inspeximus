"""
inspeximus example 02 — correction & erasure as first-class channels.

    pip install inspeximus
    python 02_correction_and_erasure.py

This is what inspeximus is built for: a corrected fact stays corrected, a restated stale value does not resurrect
it, and a delete is auditable. All zero-dependency, no LLM.
"""
from inspeximus import Inspeximus

m = Inspeximus()

# 1) ECHO GUARD — a restated OLD value must not resurrect after a correction.
m.echo_guard = True
m.remember("The meeting is on Monday", key="cal::meeting", object="Monday")
m.remember("The meeting is on Tuesday", key="cal::meeting", object="Tuesday")   # correction
m.remember("The meeting is on Monday", key="cal::meeting", object="Monday")     # a stale restatement (echo)
print("current meeting day:", [r["text"] for r in m.recall("meeting day")])     # stays Tuesday

# 2) forget() — genuinely REMOVE content (right-to-erasure), scrubbing links too.
m.remember("Contact: alice@example.com", key="contact::alice", tags=["pii"])
before = len(m.items)
res = m.forget(where=lambda r: "alice@example.com" in r["text"])
print(f"\nforgot {res['forgotten']} record(s); store went {before} -> {len(m.items)}")
print("recall after erasure:", [r["text"] for r in m.recall("alice contact")])  # gone

# 3) forget_subject() — erase everything attributable to a subject + leave a tamper-evident tombstone.
m.remember("Order 5512 shipped", source={"doc": "customer-88"})
m.remember("Order 5512 refunded", source={"doc": "customer-88"})
erased = m.forget_subject("customer-88", request_id="gdpr-req-001")
print(f"\nerased {erased['erased']} record(s) for customer-88; tombstones: {erased['tombstones']}")
print("erasure audit:", m.erasure_report()["tombstoned_total"], "tombstone(s) on record")
