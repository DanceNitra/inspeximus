"""Is the DEFAULT store of this tree the same store the previous release writes? Measured, not asserted.

WHY. 3.2.0 adds `supersession="authority"` and says the default is byte-identical to 3.1.0. A
changelog can say that without anyone checking. This runs ONE fixed write script twice, once with
the previous release's package (a wheel unpacked at --previous) and once with this tree, on stores
with surrogate ids and timestamps pinned, and compares the persisted bytes of the JSON store and of
the row store's `records` table. The script exercises the paths 3.2.0 touched: keyed writes carrying
`source.authority` in both directions, a lower-authority write after a higher one, `derived_from`
lineage, an echo, a reaffirm, a retire, a garbage authority, tenants and agents. Pinned on both
sides: uuid4 (the id), time.time (ts and valid_from), time.gmtime (the iso field) and os.urandom
(the nonce); the first run without the last two pins differed in exactly those two fields and
nothing else, which is how the pin list was found. The pinned uuid puts its counter in the HIGH
bits: the first version used the low bits, every id truncated to "0000000000", every write replaced
the one before, and the two sides compared identical on a single row. The script now asserts the
row count it wrote, so a harness that measures one row cannot report a match.

The comparison is done with the SAME interpreter in two subprocesses so nothing about this process
leaks into either side. Exit 0 when identical, 1 when they differ (with the first differing line).

    python -m pip download inspeximus==3.1.0 --no-deps -d /tmp/w && python -m zipfile -e /tmp/w/*.whl /tmp/w/site
    python probes/the_default_store_is_byte_identical_to_the_previous_release.py --previous /tmp/w/site
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r'''
import json, os, sys, sqlite3, time, random, uuid
import inspeximus.core as core
from inspeximus import Inspeximus
# pin what is random or clocked so the two sides can be compared byte for byte
_n = [0]
def _fake_uuid():
    _n[0] += 1
    return uuid.UUID(int=_n[0] << 88)       # the id is the FIRST hex chars; low bits would all read 0000000000
core.uuid.uuid4 = _fake_uuid
_t = [1_700_000_000.0]
def _fake_time():
    _t[0] += 1.0
    return _t[0]
core.time.time = _fake_time
_gmtime = time.gmtime
core.time.gmtime = lambda *a: _gmtime(_t[0])                # the `iso` field reads the wall clock
_r = [0]
def _fake_urandom(n):
    _r[0] += 1
    return _r[0].to_bytes(n, "big")
core.os.urandom = _fake_urandom                            # the per-record nonce
random.seed(0)
out = sys.argv[1]
for fmt in ("json", "sqlite"):
    p = os.path.join(out, "s." + fmt)
    ix = Inspeximus(path=p)
    ix.remember("qty is 50", key="inv::qty", object="50", source={"doc": "system", "authority": 1.0})
    ix.remember("qty is 48", key="inv::qty", object="48", source={"doc": "agent_B", "authority": 0.8})
    ix.remember("qty is 47", key="inv::qty", object="47", source={"doc": "agent_A", "authority": 0.8})
    ix.remember("qty is 49", key="inv::qty", object="49", source={"doc": "auditor", "authority": 1.0})
    r = ix.remember("a rumour: 40", source={"doc": "rumour", "authority": 0.3})
    ix.remember("summary: 40", key="inv::qty", object="40", source={"doc": "sum", "authority": 1.0}, derived_from=[r])
    ix.remember("qty is 48", key="inv::qty", object="48", source={"doc": "echo", "authority": 1.0})       # an echo
    ix.remember("qty is 48", key="inv::qty", object="48", source={"doc": "back", "authority": 0.1}, reaffirm=True)
    try:
        ix.remember("bad", key="other", object="x", source={"doc": "d", "authority": "high"})
    except ValueError:                                     # refused under the authority policy (the control arm)
        ix.remember("bad", key="other", object="x", source={"doc": "d"})
    ix.remember("legacy", key="legacy", object="L")
    ix.remember("declared", key="legacy", object="D", source={"doc": "d", "authority": 0.5})
    ix.retire("other", "ended")
    ix.for_tenant("acme").remember("acme qty 1", key="inv::qty", object="1", source={"doc": "s", "authority": 0.2})
    ix.as_agent("alice").remember("alice qty 2", key="inv::qty", object="2", source={"doc": "s", "authority": 0.9})
    ix.flush()
    n_rows = len(ix._items)
    assert n_rows == 13, f"the harness measured {n_rows} rows; the script writes 13"
    raw = open(p, "rb").read()
    if raw.startswith(b"SQLite format 3"):
        con = sqlite3.connect(p)
        rows = con.execute("SELECT * FROM records ORDER BY rowid").fetchall()
        cols = [d[0] for d in con.execute("SELECT * FROM records LIMIT 1").description]
        print(fmt, "rows", json.dumps({"cols": cols, "rows": rows}, indent=1, default=str))
    else:
        print(fmt, "text", raw.decode("utf-8"))
'''


def _run(pythonpath: str, policy: str | None = None) -> str:
    out = tempfile.mkdtemp()
    env = {**os.environ, "PYTHONPATH": pythonpath, "INSPEXIMUS_HEADS": "0"}
    env.pop("INSPEXIMUS_SUPERSESSION", None)
    if policy:
        env["INSPEXIMUS_SUPERSESSION"] = policy
    r = subprocess.run([sys.executable, "-c", SCRIPT, out], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=out)
    if r.returncode != 0:
        raise SystemExit(f"side at {pythonpath} failed:\n{r.stderr[-2000:]}")
    return r.stdout.replace(out.replace("\\", "\\\\"), "<out>").replace(out, "<out>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--previous", required=True, help="directory holding the previous release's unpacked package")
    a = ap.parse_args()
    prev = _run(os.path.abspath(a.previous))
    this = _run(ROOT)
    same = prev == this
    # CONTROL: the same script under the new policy must NOT match, or this comparison cannot fail.
    control = _run(ROOT, policy="authority")
    control_differs = control != prev
    res = {"previous": os.path.basename(os.path.abspath(a.previous)), "identical": same,   # no local paths in a committed result
           "bytes_previous": len(prev), "bytes_this": len(this),
           "control_authority_policy_differs": control_differs}
    if not control_differs:
        print("CONTROL FAILED: the authority policy produced the same bytes, so a match means nothing")
        same = False
    if not same:
        diff = list(difflib.unified_diff(prev.splitlines(), this.splitlines(), "previous", "this", lineterm="", n=1))
        res["first_differences"] = diff[:40]
        print("\n".join(diff[:40]))
    print(json.dumps({k: v for k, v in res.items() if k != "first_differences"}, indent=1))
    with open(os.path.join(ROOT, "probes", "the_default_store_is_byte_identical_to_the_previous_release.result.json"),
              "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
