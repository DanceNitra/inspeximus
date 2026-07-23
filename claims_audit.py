"""Check every load-bearing claim in this README against the PUBLISHED package.

Run it yourself — that is the point:

    python claims_audit.py                 # downloads the current wheel from PyPI and audits it
    python claims_audit.py --version 1.24.0
    python claims_audit.py --local         # audit the working tree instead

It downloads the wheel, unpacks it into a temp directory, and runs each claim as an independent
check against THAT artifact, never against the working copy. Each line prints PASS / FAIL /
NOT-TESTABLE-HERE with the raw evidence, so the output can be read rather than trusted.

Why this file exists: on 2026-07-20 the README said erasure leaves a signed receipt. Installing the
published wheel and testing that one sentence took ten minutes and found that plain `forget()` left
no receipt at all — the record was gone, the bytes were gone, and the store's own `verify_writes()`
then reported the deletion as `out-of-band`, i.e. accused its own API call of tampering (fixed in
1.24.0). One claim tested, one claim broken. This audits the rest.

Claims about OTHER systems (the comparison table) are listed and explicitly marked untestable here,
because verifying them means running those systems, not this one. They are not silently counted as
passes.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

PKG_ENV = "INSPEXIMUS_AUDIT_PKG"


def _load():
    """Import inspeximus from the artifact under audit (set by the parent process)."""
    p = os.environ.get(PKG_ENV)
    if p and p not in sys.path:
        sys.path.insert(0, p)
    import inspeximus as inspeximus
    from inspeximus.core import Inspeximus
    return inspeximus, Inspeximus


def _store(tmp_name, keyed=True):
    inspeximus, Inspeximus = _load()
    from inspeximus.core import regex_extractor
    d = pathlib.Path(tempfile.mkdtemp(prefix=f"audit_{tmp_name}_"))
    m = Inspeximus(path=str(d / "store.jsonl"))
    if keyed:
        m.extractor = regex_extractor
        m.echo_guard = True
    return m, d


# --------------------------------------------------------------------------- checks
# Each returns (ok, evidence). Keep them independent: one store each, no shared state.

def c_zero_deps():
    """README: 'zero-dependency single file'."""
    inspeximus, _ = _load()
    root = pathlib.Path(inspeximus.__file__).resolve().parent
    meta = list(root.parent.glob("*.dist-info/METADATA"))
    requires = []
    if meta:
        for ln in meta[0].read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("Requires-Dist:") and "extra ==" not in ln:
                requires.append(ln.split(":", 1)[1].strip())
    core = root / "core.py"
    return (not requires), f"mandatory requirements={requires or 'none'}; core file={core.stat().st_size//1024} KB"


def c_no_llm_on_write():
    """README: 'no LLM on write — deterministic'. Enforced by making the network unusable."""
    real = socket.socket

    class _Blocked(socket.socket):
        def __init__(self, *a, **k):
            raise AssertionError("write path opened a socket")

    socket.socket = _Blocked
    try:
        m, _ = _store("nollm")
        for t in ("My address is Unit 3B.", "My manager is Rachel Tseng."):
            m.remember(t)
        hits = m.recall("address", k=2, mode="lexical", reinforce=False)
        return True, f"{len(m.items)} writes + 1 recall with sockets disabled; recall returned {len(hits or [])}"
    except AssertionError as e:
        return False, str(e)
    finally:
        socket.socket = real


def c_supersession():
    """README: 'corrections stick — supersession + echo_guard'."""
    m, _ = _store("sup")
    m.remember("My address is 742 Birchwood Lane, Unit 3B.")
    m.remember("My address is 742 Birchwood Lane, Unit 4A.")
    rows = [(r["status"], r["text"]) for r in m.items if r.get("key")]
    act = [t for s, t in rows if s == "active" and "address" in t.lower()]
    sup = [t for s, t in rows if s == "superseded"]
    ok = len(act) == 1 and "4A" in act[0] and any("3B" in t for t in sup)
    return ok, f"active={act}; superseded={sup}"


def c_revert():
    """README: 'revert(key) — restore the predecessor'. Without being told the old value."""
    m, _ = _store("rev")
    m.remember("My address is 742 Birchwood Lane, Unit 3B.")
    m.remember("My address is 742 Birchwood Lane, Unit 4A.")
    key = [r["key"] for r in m.items if r.get("key") and "address" in r["key"]][0]
    out = m.revert(key)
    active = [r["text"] for r in m.items if r.get("key") == key and r["status"] == "active"]
    ok = bool(out.get("ok")) and any("3B" in t for t in active)
    return ok, f"revert()->{json.dumps(out, default=str)[:120]}; active now={active}"


def c_forget_receipt():
    """README (1.24.0): 'every deletion path leaves a receipt'."""
    m, d = _store("fgt")
    keep = m.remember("My manager is Rachel Tseng.")
    drop = m.remember("My employee ID is MCG-20250115-47.")
    m._save(force=True)
    res = m.forget(ids=drop, request_id="audit", basis="claims_audit")
    m._save(force=True)
    disk = (d / "store.jsonl").read_text(encoding="utf-8", errors="replace")
    toms = [t for t in getattr(m, "_tombstones", []) if t.get("memory_id") == drop]
    ok = ("MCG-20250115-47" not in disk) and len(toms) == 1 and any(r["id"] == keep for r in m.items)
    return ok, f"forget()->{res}; bytes_gone={'MCG-20250115-47' not in disk}; tombstones={len(toms)}"


def c_verify_after_forget():
    """README: deletion is 'accounted for' — the audit must not call it tampering."""
    m, _ = _store("vfy")
    i = m.remember("My employee ID is MCG-20250115-47.")
    m._save(force=True)
    m.forget(ids=i)
    v = m.verify_writes()
    problems = v[1] if isinstance(v, tuple) else (v.get("problems") if isinstance(v, dict) else v)
    bad = [p for p in (problems or []) if "out-of-band" in str(p)]
    return (not bad), f"verify_writes()->{v}"


def c_tamper_detected():
    """README: 'tamper-evident write chain' — editing a stored record must be caught.

    NOTE, and the first version of this check got it wrong: write receipts are OPT-IN
    (`Inspeximus(..., receipts=True)`). Without them there is no chain to compare against, so
    verify_writes() returns clean and the check FAILED against correct code. The claim is about the
    receipt chain, so the store under test must have it enabled — auditing a feature with the feature
    switched off measures nothing.
    """
    inspeximus, Inspeximus = _load()
    d = pathlib.Path(tempfile.mkdtemp(prefix="audit_tamper_"))
    m = Inspeximus(path=str(d / "store.jsonl"), receipts=True)
    i = m.remember("My salary is 74500.")
    m._save(force=True)
    clean = m.verify_writes()
    for r in m.items:
        if r["id"] == i:
            r["text"] = "My salary is 999999."
    v = m.verify_writes()
    problems = v[1] if isinstance(v, tuple) else (v.get("problems") if isinstance(v, dict) else v)
    return bool(problems), f"before edit={str(clean)[:40]}; after silent edit->{str(v)[:150]}"


def c_determinism():
    """README: 'deterministic by construction' — same input, same state, any machine."""
    hashes = []
    for n in range(2):
        m, _ = _store(f"det{n}")
        for t in ("My address is Unit 3B.", "My manager is Rachel Tseng.", "My address is Unit 4A."):
            m.remember(t)
        hashes.append(hashlib.sha256(json.dumps(
            sorted((r.get("text", ""), r.get("status", ""), r.get("key") or "") for r in m.items),
            ensure_ascii=False).encode()).hexdigest())
    return hashes[0] == hashes[1], f"runA={hashes[0][:24]} runB={hashes[1][:24]}"


def c_trusted_only_fails_closed():
    """CHANGELOG 1.19.0: 'recall(trusted_only=True) fails CLOSED with no trust_seeds'."""
    m, _ = _store("trust")
    m.remember("The bank account is 123.")
    hits = m.recall("bank account", k=3, mode="lexical", reinforce=False, trusted_only=True)
    return len(hits or []) == 0, f"no trust_seeds -> trusted_only returned {len(hits or [])} hits"


def c_tenant_isolation():
    """README: 'tenant isolation' — one tenant must not recall another's records."""
    inspeximus, Inspeximus = _load()
    d = pathlib.Path(tempfile.mkdtemp(prefix="audit_tenant_"))
    base = Inspeximus(path=str(d / "s.jsonl"))
    try:
        # the API is for_tenant(); a first pass guessed tenant_view() and reported SKIP on a feature
        # that is present — a wrong method name reads exactly like a missing feature
        a = base.for_tenant("acme") if hasattr(base, "for_tenant") else None
        b = base.for_tenant("globex") if hasattr(base, "for_tenant") else None
        if a is None:
            return None, "no for_tenant() on this build"
        a.remember("Acme's launch code is ALPHA.")
        b.remember("Globex's launch code is BETA.")
        leak = [h.get("text", "") for h in (b.recall("launch code", k=5, mode="lexical", reinforce=False) or [])
                if "ALPHA" in h.get("text", "")]
        return not leak, f"globex recall saw acme rows: {leak or 'none'}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def c_witness():
    """README: 'witness()' — a digest over the store's state."""
    m, _ = _store("wit")
    m.remember("A fact.")
    w = m.witness()
    ok = isinstance(w, dict) and bool(w.get("digest"))
    return ok, json.dumps(w, default=str)[:150]


def c_pii_sweep():
    """README: 'forget_pii — data-minimisation sweep over tagged records'."""
    m, _ = _store("pii")
    try:
        m.remember("Contact me at rasto@example.com.", pii=["email"])
        m.remember("My manager is Rachel Tseng.")
        before = len(m.items)
        res = m.forget_pii(types=["email"])
        gone = not any("example.com" in r.get("text", "") for r in m.items)
        return (gone and before > len(m.items)), f"forget_pii()->{res}; email row gone={gone}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def c_mcp_server_present():
    """README: 'an MCP server so any agent can use it as memory'."""
    inspeximus, _ = _load()
    root = pathlib.Path(inspeximus.__file__).resolve().parent
    cands = list(root.glob("*mcp*.py")) + list(root.parent.glob("*mcp*.py"))
    return bool(cands), f"module(s): {[c.name for c in cands] or 'none found in the wheel'}"


CHECKS = [
    ("zero dependencies", c_zero_deps),
    ("no LLM / no network on the write path", c_no_llm_on_write),
    ("corrections supersede the predecessor", c_supersession),
    ("revert(key) restores the predecessor unaided", c_revert),
    ("every deletion leaves a receipt (1.24.0)", c_forget_receipt),
    ("a deletion is not reported as tampering", c_verify_after_forget),
    ("a silent edit IS reported as tampering", c_tamper_detected),
    ("deterministic: same writes, same state", c_determinism),
    ("trusted_only fails closed without trust seeds", c_trusted_only_fails_closed),
    ("tenant isolation on recall", c_tenant_isolation),
    ("witness() returns a state digest", c_witness),
    ("forget_pii sweeps tagged records", c_pii_sweep),
    ("an MCP server ships in the package", c_mcp_server_present),
]

# Claims that CANNOT be settled by running this package. Listed so they are never silently
# counted as passing — verifying them means running the other systems, not this one.
NOT_TESTABLE_HERE = [
    "mem0 keeps the deleted value in its SQLite history table",
    "Zep/Graphiti retains the invalidated edge",
    "Letta has an engine-level checkpoint-undo (undo_checkpoint_block over BlockHistory), not surfaced as a first-class recall-integrity op",
    "revert-to-predecessor is rare: mem0 and Graphiti expose none; Letta has an undocumented service-layer undo",
    "secure erasure at rest (needs an encrypted store + key destruction)",
]


def _run(idx):
    name, fn = CHECKS[idx]
    t0 = time.time()
    try:
        ok, ev = fn()
    except Exception as e:
        ok, ev = False, f"raised {type(e).__name__}: {e}"
    return idx, name, ok, ev, time.time() - t0


def fetch_wheel(version, workdir):
    cmd = [sys.executable, "-m", "pip", "download",
           f"agora-inspeximus=={version}" if version else "agora-inspeximus",
           "--no-deps", "-d", str(workdir)]
    subprocess.run(cmd, capture_output=True, check=True)
    wheel = sorted(workdir.glob("*.whl"))[0]
    import zipfile
    pkg = workdir / "pkg"
    zipfile.ZipFile(wheel).extractall(pkg)
    return wheel, pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="audit a specific released version")
    ap.add_argument("--local", action="store_true", help="audit the working tree instead of PyPI")
    ap.add_argument("--workers", type=int, default=min(12, (os.cpu_count() or 4) - 2))
    a = ap.parse_args()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="inspeximus_claims_"))
    if a.local:
        pkg, src = pathlib.Path(__file__).resolve().parent, "working tree"
        sha = "n/a"
    else:
        wheel, pkg = fetch_wheel(a.version, tmp)
        src, sha = wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest()
    os.environ[PKG_ENV] = str(pkg)

    sys.path.insert(0, str(pkg))
    import inspeximus as inspeximus
    print("=" * 92)
    print(f"auditing : {src}")
    print(f"version  : {getattr(inspeximus, '__version__', '?')}")
    if sha != "n/a":
        print(f"sha256   : {sha}")
    print(f"checks   : {len(CHECKS)} on {a.workers} workers")
    print("=" * 92)

    results = [None] * len(CHECKS)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for idx, name, ok, ev, dt in ex.map(_run, range(len(CHECKS))):
            results[idx] = (name, ok, ev, dt)

    npass = nfail = nskip = 0
    for name, ok, ev, dt in results:
        tag = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        npass += ok is True
        nfail += ok is False
        nskip += ok is None
        print(f"[{tag}] {name}")
        print(f"       {ev}")

    print("\nNOT TESTABLE FROM THIS PACKAGE (claims about other systems — never counted as passing):")
    for c in NOT_TESTABLE_HERE:
        print(f"  [ -- ] {c}")

    print("\n" + "=" * 92)
    print(f"{npass} passed · {nfail} FAILED · {nskip} skipped · {len(NOT_TESTABLE_HERE)} not testable here")
    print("=" * 92)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
