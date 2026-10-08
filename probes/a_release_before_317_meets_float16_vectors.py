"""A release before 3.17 and 3.17 share one row store that holds float16 vectors. Both directions, measured.

WHAT CHANGED. From 3.17 a row store writes a vector as base64 float16 under `vec16`, not as a JSON list
under `vec`. An older release does not know the key. The design rests on two claims about that release,
and this probe runs the released wheel to check them instead of assuming them:

  1. it NEVER CORRUPTS the new form: every row it rewrites keeps `vec16`, and `verify_writes` holds;
  2. it IGNORES it: those records rank lexically for that release, and it raises nothing.

USAGE. The older release runs in its own interpreter, so the probe takes it as a command:

    python probes/a_release_before_317_meets_float16_vectors.py \\
        [--old "uvx --default-index https://pypi.org/simple --from inspeximus==3.16.5 python"]

The default is that command. Every step prints one JSON line; the probe asserts on them and writes the
receipt beside itself.

THE SCENARIOS
  A. 3.17 writes the store. The older release then writes with persistence on (a new record, a supersede
     of a record that holds `vec16`, an erasure), then with persistence off and no embedder (the same
     three), then runs `reembed`. 3.17 reads after each, and finally runs `compact_vectors`.
  B. The older release writes the store with list vectors. 3.17 writes into it, and the older release
     reads it back.

CONTROLS. Each scenario first checks that the format it is testing is on disk (A: rows hold `vec16`
before the older release touches them; B: rows hold lists before 3.17 does), or the run would pass on
a store that never held the case.
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OLD = "uvx --default-index https://pypi.org/simple --from inspeximus==3.16.5 python"

# One step, run under either interpreter. argv: step store_path [tree]. A tree is put first on sys.path.
STEP = r'''
import json, sqlite3, sys
step, path = sys.argv[1], sys.argv[2]
if len(sys.argv) > 3:
    sys.path.insert(0, sys.argv[3])
import inspeximus
from inspeximus import Inspeximus
RECIPE = "probe-model"   # a named recipe, so 3.17 writes vec16 as <tag>:<base64>
def emb(t):
    h = sum(map(ord, t))
    return [((h * (i + 3)) % 97) / 97.0 - 0.5 for i in range(16)]
def disk():
    con = sqlite3.connect(path)
    try:
        docs = [json.loads(r[0]) for r in con.execute("SELECT doc FROM records ORDER BY ord")]
    finally:
        con.close()
    return {(d.get("key") or d["id"]) + ":" + d.get("status", "?"):
            ("both" if "vec16" in d and isinstance(d.get("vec"), list) else
             "vec16+tag" if ":" in str(d.get("vec16", ""))[:9] else "vec16" if "vec16" in d
             else "list" if isinstance(d.get("vec"), list) else "none") for d in docs}
out = {"step": step, "file": inspeximus.__file__}
if step == "seed":
    m = Inspeximus(path=path, embed=emb, embed_id=RECIPE, persist_vectors=True, receipts=True)
    for i in range(6):
        m.remember("seed fact %d about the deploy window" % i, key="k%d" % i)
    m.flush()
elif step in ("touch", "touch_off"):
    on = step == "touch"
    m = Inspeximus(path=path, embed=emb if on else None, embed_id=RECIPE if on else None,
                    persist_vectors=on, receipts=True)
    sup, gone = ("k1", "k2") if on else ("k3", "k4")
    out["recall"] = len(m.recall("seed fact about the deploy window", k=3))
    m.remember("written by " + step, key="new_" + step)
    m.remember("superseded by " + step, key=sup)
    for rid in [r["id"] for r in m.items if r.get("key") == gone and r.get("status") == "active"]:
        m.forget(rid)
    m.flush()
elif step == "reembed":
    m = Inspeximus(path=path, embed=emb, embed_id=RECIPE, persist_vectors=True, receipts=True)
    out["reembed"] = {k: v for k, v in m.reembed(only_missing=True).items() if k != "warning"}
elif step == "compact":
    m = Inspeximus(path=path, embed=emb, embed_id=RECIPE, persist_vectors=True, receipts=True)
    out["compact"] = m.compact_vectors()
m = Inspeximus(path=path, embed=emb, embed_id=RECIPE, persist_vectors=True, receipts=True)
out["vectors_in_memory"] = sum(1 for r in m.items if isinstance(r.get("vec"), list) and r["vec"])
out["records"] = len(m.items)
out["recall_after"] = len(m.recall("seed fact about the deploy window", k=3))
ok, probs = m.verify_writes()
out["verify_writes"], out["problems"] = ok, probs[:3]
out["disk"] = disk()
print("STEP" + json.dumps(out, sort_keys=True))
'''


def run(cmd, step, path, tree=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_KEY_HOME=KEY_HOME, INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    args = cmd + ["-E", "-c", STEP, step, path] + ([tree] if tree else [])
    # NOT FROM THE TEMP DIRECTORY (AUDIT-A, 2026-10-08). Run from tempfile.gettempdir(), each step's key home, a folder
    # under it, was inside the project the step ran in; the F-13 rule ignored it with one stderr line, which this probe
    # discarded, and every step wrote its key and chain head into the owner's real key home (6 pairs on 10-07 and
    # 10-08). The steps run from WORK, a sibling of KEY_HOME, and a refusal line fails the probe.
    r = subprocess.run(args, cwd=WORK, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    if "INSPEXIMUS_KEY_HOME" in r.stderr and "is ignored" in r.stderr:
        raise SystemExit("step %s: the key home was refused, so the step wrote into the real one:\n%s"
                         % (step, r.stderr[-800:]))
    if "STEP" not in r.stdout:
        raise SystemExit("step %s failed under %s:\n%s" % (step, " ".join(cmd), r.stderr[-1500:]))
    return json.loads(r.stdout.split("STEP", 1)[1])


def _real_key_home_names() -> set:
    """Every file under the owner's key home, in both views: the real folder, and the Microsoft Store Python's redirected
    copy, which a Store Python merges into the real one and Git Bash cannot see."""
    import glob
    sys.path.insert(0, ROOT)
    from inspeximus._keyhome import default_home
    roots = [os.path.join(default_home(), "inspeximus")]
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    roots += glob.glob(os.path.join(local, "Packages", "PythonSoftwareFoundation.Python.*", "LocalCache", "Roaming",
                                    "inspeximus"))
    names = set()
    for root in roots:
        for dp, _d, files in os.walk(root):
            names |= {os.path.join(dp, f) for f in files}
    return names


def _ours(name, stores) -> bool:
    """A key or head file is named by a hash of its store's path. Is `name` one of this probe's stores'?"""
    import hashlib
    tags = {hashlib.sha256(os.path.abspath(p).encode("utf-8", "replace")).hexdigest()[:16] for p in stores}
    return os.path.basename(name)[:16] in tags


def main():
    real_before = _real_key_home_names()
    old = shlex.split(sys.argv[sys.argv.index("--old") + 1] if "--old" in sys.argv else DEFAULT_OLD)
    new = [sys.executable]
    log = []

    def step(who, name, path):
        out = run(new if who == "new" else old, name, path, ROOT if who == "new" else None)
        out["by"] = who
        log.append(out)
        print("  %-3s %-9s verify=%-5s vectors=%-2d recall=%d  %s" % (
            who, name, out["verify_writes"], out["vectors_in_memory"], out["recall_after"],
            " ".join("%s=%s" % kv for kv in sorted(out["disk"].items()))))
        return out

    d = tempfile.mkdtemp()
    a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
    print("A. 3.17 writes; the older release meets float16 vectors")
    s = step("new", "seed", a)
    assert set(s["disk"].values()) == {"vec16+tag"}, "CONTROL: 3.17 did not write tagged float16, so A tests nothing"
    o1 = step("old", "touch", a)
    assert o1["file"] != log[0]["file"], "CONTROL: both steps imported the same package"
    assert o1["disk"]["k1:superseded"] == "vec16+tag", "the older release dropped vec16 from a row it rewrote"
    assert all(o1["disk"][k] == "vec16+tag" for k in ("k0:active", "k3:active", "k4:active", "k5:active"))
    n1 = step("new", "read", a)
    assert n1["vectors_in_memory"] == 7, "3.17 lost a vector after the older release wrote"
    o2 = step("old", "touch_off", a)
    assert o2["disk"]["k3:superseded"] == "vec16+tag", "a no-persist older handle dropped vec16 from a row"
    step("new", "read", a)
    o3 = step("old", "reembed", a)
    n3 = step("new", "read", a)
    assert n3["vectors_in_memory"] == n3["records"]
    c = step("new", "compact", a)
    assert not set(c["disk"].values()) & {"list", "both"}, "compact_vectors left a list on disk"
    print("B. the older release writes list vectors; 3.17 writes into the store")
    s = step("old", "seed", b)
    assert set(s["disk"].values()) == {"list"}, "CONTROL: the older release did not write lists"
    n = step("new", "touch", b)
    assert n["vectors_in_memory"] == n["records"], "3.17 lost a list vector it read"
    o = step("old", "read", b)
    step("new", "touch_off", b)
    o4 = step("old", "read", b)

    bad = [s for s in log if not s["verify_writes"]]
    assert not bad, "verify_writes failed: %s" % [(s["by"], s["step"], s["problems"]) for s in bad]
    assert all(s["recall_after"] > 0 for s in log), "a recall came back empty"
    out = {"old": " ".join(old), "steps": log,
           "older_release_ranks_float16_rows_lexically": o["vectors_in_memory"] == 0,
           "older_release_reembed_writes_a_list_beside_vec16": "both" in o3["disk"].values(),
           "b_final_vectors_seen_by_older_release": o4["vectors_in_memory"]}
    # NOTHING IN THE REAL KEY HOME. A new file there that this probe's stores name fails it; a new file of another
    # writer (a live hook beside the run) is printed, not failed, because its name does not hash from our stores.
    new_in_real = sorted(_real_key_home_names() - real_before)
    ours = [n for n in new_in_real if _ours(n, (a, b))]
    assert not ours, "the probe wrote into the real key home: %s" % ours[:5]
    if new_in_real:
        print("  note: %d new file(s) in the real key home from other writers during the run" % len(new_in_real))
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(out, indent=1, sort_keys=True))
    print("\n  every assertion held; receipt: %s" % os.path.basename(path))
    return 0


KEY_HOME = tempfile.mkdtemp(prefix="vec16-keys-")
WORK = tempfile.mkdtemp(prefix="vec16-work-")             # the steps' working directory; KEY_HOME is not under it
if __name__ == "__main__":
    raise SystemExit(main())
