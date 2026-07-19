"""route_add_update_delete_noop_probe.py — one-call route() now emits mem0-parity ADD/UPDATE/DELETE/NOOP.

route(text) is the single-call write router: it decides remember (ADD), keyed supersession (UPDATE), dedup
(NOOP — skip re-writing the current value), delete (DELETE — capability-gated so content can't destroy memory),
or revert. This makes mnemo a deterministic, zero-LLM drop-in for mem0's add() reconcile UX. Asserts (each can FAIL):
  1. ADD: a new keyed fact -> event ADD, becomes the active value.
  2. UPDATE: a new value for the same key -> event UPDATE, supersedes (active = new).
  3. NOOP: re-routing the CURRENT value -> event NOOP and NO new record is written (dedup, not a duplicate).
  4. DELETE is capability-gated: with an authority set, an unauthorized "forget that" -> authorization_required
     (the content-can't-destroy-memory moat holds).
  5. DELETE with the right capability -> deleted, the key's active record is forgotten.
  6. revert still works (regression): "go back" restores the prior value.
"""
import sys
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

def active(m, key):
    r = [x for x in m.items if x.get("key") == key and x.get("status") == "active"]
    return (r[-1].get("meta") or {}).get(  # object stored where? fall back to text
        "object") if r else None

# 1-3 + 6 on a default store (revert/delete authorized by default)
m = Mnemo(path=None)
a = m.route("the deploy channel is BLUE-9", key="deploy", object="BLUE-9")
act1 = [x for x in m.items if x.get("key") == "deploy" and x.get("status") == "active"]
check("1 ADD: new keyed fact -> event ADD + active = BLUE-9",
      a.get("event") == "ADD" and len(act1) == 1 and "BLUE-9" in act1[0]["text"])
u = m.route("the deploy channel is RED-2", key="deploy", object="RED-2")
cur = [x for x in m.items if x.get("key") == "deploy" and x.get("status") == "active"]
check("2 UPDATE: supersedes -> event UPDATE, exactly one active = RED-2",
      u.get("event") == "UPDATE" and len(cur) == 1 and "RED-2" in cur[0]["text"])
before = len(m.items)
n = m.route("the deploy channel is RED-2", key="deploy", object="RED-2")   # same current value again
check("3 NOOP: re-routing current value -> event NOOP + NO new record",
      n.get("event") == "NOOP" and len(m.items) == before)
rv = m.route("actually go back to what we had", key="deploy")
still = [x for x in m.items if x.get("key") == "deploy" and x.get("status") == "active"]
check("6 revert still works: restores BLUE-9", any("BLUE-9" in x["text"] for x in still))

# 4-5: DELETE gated by capability (moat)
mg = Mnemo(path=None, revert_authority="s3cr3t")
mg.route("plan is alpha", key="plan", object="alpha")
mg.route("plan is beta", key="plan", object="beta")
d_unauth = mg.route("forget that plan", key="plan")                        # no capability
check("4 DELETE unauthorized -> authorization_required (moat holds)",
      d_unauth.get("action") == "authorization_required" and d_unauth.get("event") == "DELETE"
      and any(x.get("key") == "plan" and x.get("status") == "active" for x in mg.items))
d_auth = mg.route("forget that plan", key="plan", capability=mg.revert_capability("plan"))
check("5 DELETE authorized -> deleted, no active 'plan' left",
      d_auth.get("action") == "deleted" and d_auth.get("forgotten", 0) >= 1
      and not any(x.get("key") == "plan" and x.get("status") == "active" for x in mg.items))

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
