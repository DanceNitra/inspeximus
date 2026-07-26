"""Reproduce the 2026-07-26 self-audit findings from PUBLISHED packages.

Nothing here reads this repository. It exercises whatever inspeximus is INSTALLED in the current
environment and prints what that version does, so the same script run under an old version and a new one
produces the before/after columns the write-up cites. "It works on my machine" cannot creep in.

    pip install inspeximus==1.67.0 && python reproduce.py     # the before column
    pip install inspeximus==1.72.0 && python reproduce.py     # the after column

or run ./reproduce.sh, which builds a venv per version and prints the whole matrix.

Each finding is guarded: for the versions where a defect CRASHES, the traceback is the result, and the
script records it rather than dying. Where a fixture cannot exercise a finding on a given version (for
example a record that never graduates emits no amendment), it says so instead of printing a number that
means something else -- that exact confusion produced a false claim in the first draft of this audit.
"""
import contextlib
import io as _io
import json
import os
import tempfile
import traceback

import inspeximus
from inspeximus import Inspeximus

V = inspeximus.__version__
OUT = {"version": V, "findings": {}}


def record(name, **kv):
    OUT["findings"][name] = kv
    bits = "  ".join(f"{k}={v!r}" for k, v in kv.items())
    print(f"[{V}] {name:34s} {bits}")


def guard(name, fn):
    try:
        fn()
    except Exception as e:                       # a crash IS the finding for some of these
        record(name, raised=f"{type(e).__name__}: {str(e)[:60]}")


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _graduated(receipts=True):
    m = Inspeximus(path=_path(), receipts=receipts)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    for _ in range(20):
        m.credit([rid], outcome=True)
    return m, rid, next(r for r in m.items if r["id"] == rid)["mtype"]


# ── 1. a public slash() laundering an out-of-band tamper (fixed 1.68.0) ─────────────────────────────
def f1():
    m = Inspeximus(path=_path(), receipts=True)
    mid = m.remember("Revenue is 100M", mtype="semantic", source={"doc": "bigfour-auditor.com"})
    next(r for r in m.items if r["id"] == mid)["text"] = "Revenue is 900M"
    m._save(force=True)
    before = m.verify_writes()[0]
    m.slash([mid], scope="memory")
    after = m.verify_writes()[0]
    served = next(r for r in m.items if r["id"] == mid)["text"]
    record("1_slash_launders_a_tamper", after_tamper=before, after_public_slash=after, text_served=served)


# ── 2. the absolute revert path (fixed 1.69.0) ──────────────────────────────────────────────────────
def f2():
    m = Inspeximus(path=_path())
    m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    m.remember("wallet is 0xBBB", key="payout::wallet", object="0xBBB")
    res = m.submit_revert("restore:payout::wallet=0xAAA#deadbeef")
    record("2_absolute_restore", ok=res.get("ok"), current=m._route_chain("payout::wallet")[-1])


def f2b():
    m = Inspeximus(path=_path())
    m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    m.remember("wallet is 0xBBB", key="payout::wallet", object="0xBBB")
    res = m.restore_now("payout::wallet", "0xAAA")
    record("2b_restore_now", ok=res.get("ok"))


# ── 3. the certificate reporting valid without the absence proof (fixed 1.70.0) ─────────────────────
def f3():
    import inspeximus.core as core
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.json")
    m = Inspeximus(path=p, receipts=True)
    rid = m.remember("alice lives at 12 Oak St")
    m.forget(ids=[rid])
    m.flush()
    cert = m.erasure_certificate()
    good = core.verify_erasure_certificate(cert, store_path=p)
    typo = core.verify_erasure_certificate(cert, store_path=os.path.join(d, "TYPO.json"))
    record("3_certificate_absence_proof",
           correct_path=good["valid"], typo_path=typo["valid"],
           typo_store_absent=typo["checks"]["store_absent"])


# ── 4. the Claude Code installer overwriting an unparseable settings.json (fixed 1.71.0) ────────────
def f4():
    import inspeximus.claude_code as cc
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".claude"))
    p = os.path.join(d, ".claude", "settings.json")
    _io.open(p, "w", encoding="utf-8").write(
        '{"model":"opus","permissions":{"allow":["Bash(git:*)"]},'
        '"hooks":{"PostToolUse":[{"matcher":"Edit","hooks":[{"type":"command","command":"my-linter"}]}]},}')
    before = _io.open(p, encoding="utf-8").read()
    with contextlib.redirect_stdout(_io.StringIO()):
        try:
            cc.install(cwd=d)
        except Exception as e:
            record("4_installer_on_broken_config", raised=type(e).__name__)
            return
    after = _io.open(p, encoding="utf-8").read()
    record("4_installer_on_broken_config",
           file_unchanged=(after == before),
           user_model_survived=("opus" in after),
           user_hook_survived=("my-linter" in after))


# ── 5. anchor truncation and an unverifiable bundle (fixed 1.72.0) ──────────────────────────────────
def f5():
    from inspeximus.audit_bundle import build_bundle, verify_bundle
    m, rid, mtype = _graduated()
    if mtype != "semantic":
        record("5_anchor_and_bundle", skipped="fixture did not graduate; no amendment is emitted")
        return
    m.slash([rid], scope="memory")
    m.flush()
    a = m.anchor()
    res = verify_bundle(build_bundle(m))
    record("5_anchor_and_bundle",
           receipts=len(m._receipts), anchor_n_writes=a.get("n_writes"),
           verify_writes=m.verify_writes()[0], verify_bundle=res.get("ok"))


for name, fn in (("1", f1), ("2", f2), ("2b", f2b), ("3", f3), ("4", f4), ("5", f5)):
    guard(f"finding_{name}", fn)

print()
print(json.dumps(OUT, indent=2, default=str))
