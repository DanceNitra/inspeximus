"""langgraph_saver_probe.py — MnemoSaver is a working LangGraph BaseCheckpointSaver (thread-state persistence).

mnemo already had MnemoStore (long-term BaseStore); this is the other half — the checkpointer that lets a graph
resume. Round-trips the real BaseCheckpointSaver contract against a mnemo file. Asserts (each able to FAIL):
  1. put() a checkpoint then get_tuple() returns the SAME checkpoint (state survives).
  2. put_writes() then get_tuple().pending_writes carries them back.
  3. list() yields the thread's checkpoints (newest-first).
  4. parent_config threads correctly (a 2nd checkpoint points back to the 1st).
  5. delete_thread() removes them (get_tuple -> None).
"""
import os, sys, tempfile
os.environ["MNEMO_EMBED_URL"] = ""                 # lexical, no GPU
sys.path.insert(0, ".")
from langgraph.checkpoint.base import empty_checkpoint, create_checkpoint
from mnemo.integrations.langgraph import MnemoSaver

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

p = os.path.join(tempfile.gettempdir(), "lg_saver_probe.json")
if os.path.exists(p): os.remove(p)
saver = MnemoSaver(path=p)

cfg = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
c1 = empty_checkpoint()
c1["channel_values"]["topic"] = "alpha"
nc = saver.put(cfg, c1, {"source": "input", "step": 0}, {})
check("1 put+get_tuple round-trips the checkpoint state",
      saver.get_tuple(nc).checkpoint["channel_values"].get("topic") == "alpha")

saver.put_writes(nc, [("topic", "beta"), ("count", 7)], task_id="task1")
pend = saver.get_tuple(nc).pending_writes or []
check("2 put_writes round-trips via pending_writes",
      any(ch == "topic" and val == "beta" for (_tid, ch, val) in pend))

c2 = create_checkpoint(c1, None, 1)
c2["channel_values"]["topic"] = "gamma"
nc2 = saver.put(nc, c2, {"source": "loop", "step": 1}, {})
tuples = list(saver.list(cfg))
check("3 list() yields the thread's checkpoints", len(tuples) == 2)
check("4 newest-first + parent threads back to prior",
      tuples[0].checkpoint["channel_values"].get("topic") == "gamma"
      and tuples[0].parent_config["configurable"]["checkpoint_id"] == c1["id"])

saver.delete_thread("t1")
check("5 delete_thread removes the thread (get_tuple -> None)", saver.get_tuple(cfg) is None)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
