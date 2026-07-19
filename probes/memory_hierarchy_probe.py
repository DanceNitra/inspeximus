"""memory_hierarchy_probe.py — user > agent > session memory scoping (mem0/Letta-style), deterministic.

remember(user_id/agent_id/session_id) stamps a memory's scope; recall(user_id/agent_id/session_id) filters by
HIERARCHICAL VISIBILITY: a named query level must match (or the memory is unscoped there); an unnamed level is
unconstrained. Asserts (each able to FAIL):
  1. a session query sees its OWN session memory + the user-level shared memory, but NOT a PEER session's.
  2. user isolation: a query for user V never returns user U's memories.
  3. a user-only query sees that user's own session memories too (same user, not a leak).
  4. unscoped/global memory is visible to any scoped query; a legacy no-id recall still sees everything.
"""
import sys
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

def fresh():
    m = Mnemo(path=None)                                    # lexical, no GPU
    m.remember("shared coffee preference is black", user_id="U")                       # user-level (U)
    m.remember("coffee note from session one is latte", user_id="U", session_id="S1")  # U / S1
    m.remember("coffee note from session two is espresso", user_id="U", session_id="S2")  # U / S2
    m.remember("coffee preference for user V is tea", user_id="V")                     # user V
    m.remember("global coffee fact applies to all", )                                  # unscoped/global
    return m

def texts(hits): return " || ".join(h["text"] for h in hits)

# 1. session S1 query: own session + user-shared + global; NOT peer session S2, NOT user V
h = fresh().recall("coffee", k=10, user_id="U", session_id="S1")
t = texts(h)
check("1 session query sees own+shared+global, not peer session / other user",
      "latte" in t and "black" in t and "global" in t and "espresso" not in t and "tea" not in t)

# 2. user isolation: V query never sees U's memories
h2 = fresh().recall("coffee", k=10, user_id="V")
t2 = texts(h2)
check("2 user isolation: V sees only V (+global), never U", "tea" in t2 and "latte" not in t2 and "black" not in t2 and "espresso" not in t2)

# 3. user-only query sees that user's own session memories
h3 = fresh().recall("coffee", k=10, user_id="U")
t3 = texts(h3)
check("3 user-only query sees the user's own sessions", "black" in t3 and "latte" in t3 and "espresso" in t3 and "tea" not in t3)

# 4. legacy no-id recall sees everything (no regression)
h4 = fresh().recall("coffee", k=10)
check("4 legacy recall (no ids) sees all 5", len(h4) == 5)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
