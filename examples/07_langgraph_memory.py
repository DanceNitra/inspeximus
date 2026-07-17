"""mnemo as a LangGraph long-term memory store — same BaseStore API, plus the part LangGraph's
built-in store throws away: what the value USED to be.

LangGraph agents persist long-term memory through a BaseStore (put/get/search over (namespace, key)).
The built-in InMemoryStore is last-write-wins with no history: a second put on the same key silently
destroys the first. MnemoStore is a drop-in BaseStore with identical semantics for put/get/search/delete —
and underneath, every overwrite is a mnemo supersession, so you ALSO get:

  * history(namespace, key)  — every value the key has held, in order (audit / debugging / trust)
  * a correction that stays corrected — the ledger knows which value is current vs retired
  * forget_subject erasure + tamper-evident receipts when governance asks "prove it's gone"

Swap ONE line in an existing LangGraph app:

    - from langgraph.store.memory import InMemoryStore
    - store = InMemoryStore()
    + from mnemo.integrations.langgraph import MnemoStore
    + store = MnemoStore(path="agent_memory.json")        # persists across restarts, too

Run:  pip install "agora-mnemo" langgraph  &&  python examples/07_langgraph_memory.py
"""
from mnemo.integrations.langgraph import MnemoStore


def main():
    store = MnemoStore()          # pass path="agent_memory.json" to persist across process restarts

    ns = ("user", "42")

    # The agent learns a preference, then the user corrects it later — the classic memory lifecycle.
    store.put(ns, "timezone", {"tz": "UTC", "source": "onboarding"})
    store.put(ns, "plan", {"tier": "starter"})
    store.put(ns, "timezone", {"tz": "America/New_York", "source": "user correction"})

    # Identical BaseStore reads an existing LangGraph app already does:
    print("get      :", store.get(ns, "timezone").value)          # current value only — the correction holds
    print("search   :", [i.key for i in store.search(ns)])        # both keys, current values

    # What InMemoryStore cannot answer: what did we believe before, and when did it change?
    print("history  :", store.history(ns, "timezone"))

    # Deletion is real deletion, with a receipt trail on the mnemo side.
    store.delete(ns, "plan")
    print("deleted  :", store.get(ns, "plan"))

    print("\nWhy this matters for agents: when a user corrects a fact, InMemoryStore forgets the old value "
          "ever existed — so you can't audit what the agent believed when it acted, and a re-learned stale "
          "value looks identical to fresh truth. MnemoStore keeps the supersession ledger underneath the "
          "same API, so the current value is served, the past is queryable, and erasure is provable.")


if __name__ == "__main__":
    main()
