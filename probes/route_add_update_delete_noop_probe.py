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
import sys, os, tempfile
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


# ── 1.18.1 regressions: the delete branch used to be reachable by content alone ──────────────────────
# 6: on a DEFAULT store (no revert_authority/revert_pubkey) _revert_authorized returns True ("legacy"), so
# route("forget that X") hard-deleted the whole ledger for X. Safe for revert (version-graph only), fatal for
# an irreversible forget(). Deleting now requires an authority to be CONFIGURED, then satisfied.
mu = Mnemo(path=os.path.join(tempfile.mkdtemp(), "u.json"))
mu.route("my address is Baker Street", key="address", object="Baker Street")
du = mu.route("forget that address")
check("6 ungated store refuses a routed delete (content alone cannot destroy memory)",
      du.get("action") == "authorization_required"
      and sum(1 for x in mu.items if x.get("key") == "address" and x.get("status") == "active") == 1)

# 7: the delete vocabulary overlaps corrections and reverts, and used to be tested FIRST, so a value-bearing
# correction and a revert utterance were both swallowed as deletes and their writes never happened.
mo = Mnemo(path=os.path.join(tempfile.mkdtemp(), "o.json"))
mo.route("region is eu", key="region", object="eu")
c7 = mo.route("drop the beta flag; region is now us-east", key="region", object="us-east")
check("7 a value-bearing correction is stored, not swallowed as a delete",
      c7.get("action") == "remembered" and c7.get("id"))
mv = Mnemo(path=os.path.join(tempfile.mkdtemp(), "v.json"))
mv.route("colour is red", key="colour", object="red")
mv.route("colour is blue", key="colour", object="blue")
check("7b a revert-marked utterance routes to revert, not delete",
      mv.route("undo that, it is no longer valid").get("intent") == "revert")

# 8: every other branch returns an id; NOOP omitted the key entirely, so callers KeyError'd on a duplicate.
mn = Mnemo(path=os.path.join(tempfile.mkdtemp(), "n.json"))
mn.route("x is 1", key="x", object="1")
n8 = mn.route("x is 1", key="x", object="1")
check("8 NOOP carries an explicit id=None (no KeyError for callers reading ['id'])",
      n8.get("action") == "noop" and "id" in n8 and n8["id"] is None)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
