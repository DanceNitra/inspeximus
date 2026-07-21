"""Break the one sentence we intend to say in public, before we say it.

The claim: *"tell mnemo to forget everything about a person, and it not only does it — it can prove
it."* `claims_audit.py` checks that the API exists and behaves on a single record. This goes after the
claim as a buyer would use it, and tries to falsify it:

  - erasure across DERIVED records (a summary built from the subject's data must go too)
  - nothing left in the store, in recall, in the links of surviving records, or in the BYTES of any
    file the store wrote — including its sidecars
  - a receipt that exists, verifies, and DETECTS tampering
  - the erasure survives a reload from disk (a store that only forgets in RAM forgets nothing)
  - unrelated records survive (a delete that takes the neighbours with it is not a feature)
  - the whole thing is reproducible: every scenario runs THREE times and must land on the same state

Three scenarios, three repeats each. Any single failure fails the claim — the sentence is either true
every time or it is not a sentence we get to use.

    python governance_audit.py            # audits the published wheel from PyPI
    python governance_audit.py --local    # audits the working tree
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

PKG_ENV = "MNEMO_AUDIT_PKG"

# (subject, records the subject owns, a derived record built FROM them, unrelated records that must live)
SCENARIOS = [
    {
        "name": "colleague leaves the company",
        "subject": "david-lam",
        "owned": ["David Lam sits in Strategic Analytics and reviews the MedVantage deliverable.",
                  "David Lam's desk phone is 555-0134.",
                  "David Lam prefers morning stand-ups."],
        "derived": "Summary: David Lam reviews MedVantage and prefers morning stand-ups.",
        "unrelated": ["My manager is Rachel Tseng.",
                      "The MedVantage deliverable is due quarterly."],
        "probe_queries": ["who reviews MedVantage", "david lam", "desk phone", "stand-ups"],
    },
    {
        "name": "customer exercises right to erasure",
        "subject": "customer-4417",
        "owned": ["Customer 4417 is Jana Kovacova, jana.kovacova@example.com.",
                  "Customer 4417 lives at Hlavna 12, Nitra.",
                  "Customer 4417 complained about invoice INV-9981."],
        "derived": "Summary: customer 4417 (Jana Kovacova) had an invoice complaint.",
        "unrelated": ["Invoice INV-9981 was issued in March.",
                      "Our support SLA is 24 hours."],
        "probe_queries": ["jana kovacova", "customer 4417", "hlavna 12", "who complained"],
    },
    {
        "name": "credential must disappear",
        "subject": "legacy-api-key",
        "owned": ["The legacy API key is sk-legacy-ZZZ9911.",
                  "The legacy API key rotates every 90 days.",
                  "The legacy API key is used by the billing job."],
        "derived": "Summary: the legacy API key sk-legacy-ZZZ9911 is used by billing.",
        "unrelated": ["The billing job runs at 02:00 UTC.",
                      "Rotation policy is 90 days for all credentials."],
        "probe_queries": ["api key", "sk-legacy", "billing job", "rotation"],
    },
]


def _load():
    p = os.environ.get(PKG_ENV)
    if p and p not in sys.path:
        sys.path.insert(0, p)
    import inspeximus as mnemo
    from mnemo.mnemo import Mnemo
    return mnemo, Mnemo


def _secrets(sc):
    """The literal strings that must not survive anywhere."""
    out = []
    for t in sc["owned"] + [sc["derived"]]:
        for tok in ("sk-legacy-ZZZ9911", "555-0134", "jana.kovacova@example.com",
                    "Hlavna 12", "David Lam", "Jana Kovacova"):
            if tok.lower() in t.lower():
                out.append(tok.lower())
    return sorted(set(out))


def state_hash(m):
    return hashlib.sha256(json.dumps(
        sorted((r.get("text", ""), r.get("status", ""), r.get("key") or "") for r in m.items),
        ensure_ascii=False).encode()).hexdigest()


def run_scenario(sc, run_idx):
    """One full build → erase → verify cycle. Returns (checks: dict[str, bool], evidence: list[str])."""
    _, Mnemo = _load()
    d = pathlib.Path(tempfile.mkdtemp(prefix=f"gov_{run_idx}_"))
    ev, chk = [], {}
    m = Mnemo(path=str(d / "store.jsonl"), receipts=True)

    owned_ids = [m.remember(t, source={"doc": sc["subject"]}) for t in sc["owned"]]
    m.remember(sc["derived"], source={"doc": sc["subject"] + "-summary"}, derived_from=owned_ids,
               derived=True)
    for t in sc["unrelated"]:
        m.remember(t, source={"doc": "other"})
    m._save(force=True)
    n_before = len(m.items)

    if os.environ.get("GOV_FALSIFY") == "1":
        # Falsification mode: skip the erasure entirely. If the checks below still pass, they are
        # measuring nothing and the whole audit is theatre. A test that cannot fail is a demo.
        res = {"erased": 0, "ids": [], "tombstones": 0}
        ev.append("FALSIFY MODE: erasure deliberately NOT performed")
    else:
        res = m.forget_subject(sc["subject"], request_id=f"gdpr-{run_idx}",
                               basis="right to erasure (art. 17)")
        ev.append(f"forget_subject -> {json.dumps(res, default=str)[:150]}")
    m._save(force=True)

    secrets = _secrets(sc)

    # 1. gone from the store
    blob = " ".join((r.get("text") or "") + " " + str(r.get("object") or "") for r in m.items).lower()
    chk["erased from records"] = not any(s in blob for s in secrets)

    # 2. gone from recall, across several phrasings
    hit = []
    for q in sc["probe_queries"]:
        ctx = " ".join(h.get("text", "") for h in
                       (m.recall(q, k=10, mode="lexical", reinforce=False) or [])).lower()
        hit += [s for s in secrets if s in ctx]
    chk["not recallable"] = not hit
    if hit:
        ev.append(f"RECALL LEAK: {sorted(set(hit))}")

    # 3. the derived record went too (this is the lineage claim)
    chk["derived record erased"] = not any(sc["derived"][:40].lower() in (r.get("text") or "").lower()
                                           for r in m.items)

    # 4. unrelated records survived
    kept = sum(1 for t in sc["unrelated"]
               if any(t[:30].lower() in (r.get("text") or "").lower() for r in m.items))
    chk["unrelated survived"] = kept == len(sc["unrelated"])
    ev.append(f"records {n_before} -> {len(m.items)}; unrelated kept {kept}/{len(sc['unrelated'])}")

    # 5. nothing in the BYTES of any file the store wrote (store + every sidecar)
    residue = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            body = f.read_text(encoding="utf-8", errors="replace").lower()
            residue += [f"{f.name}:{s}" for s in secrets if s in body]
    chk["no residue in any file"] = not residue
    if residue:
        ev.append(f"BYTES RESIDUE: {residue[:4]}")

    # 6. EXACTLY one receipt per erased record — not "at least one".
    # The first version of this check used >= and passed a real regression: forget_subject() called
    # forget() and then emitted its own tombstones, so after 1.24.0 every record got TWO receipts,
    # one carrying the real basis and one carrying a generic "forget". A lenient assertion is how a
    # test agrees with a bug.
    toms = getattr(m, "_tombstones", [])
    n_erased = res.get("erased", 0)
    chk["exactly one receipt per erased record"] = n_erased > 0 and len(toms) == n_erased
    bases = sorted({(t.get("auth") or {}).get("basis") for t in toms})
    chk["receipt carries the caller's basis"] = bases == ["right to erasure (art. 17)"]
    ev.append(f"tombstones={len(toms)} for {n_erased} erased; bases={bases}")

    # 7. the audit does not call the erasure tampering
    v = m.verify_writes()
    problems = v[1] if isinstance(v, tuple) else v
    chk["audit clean after erasure"] = not [p for p in (problems or []) if "out-of-band" in str(p)]

    # 8. tampering with a receipt IS detected
    if toms:
        keep = toms[0].get("hash")
        toms[0]["hash"] = "0" * 64
        v2 = m.verify_writes()
        p2 = v2[1] if isinstance(v2, tuple) else v2
        chk["tampered receipt detected"] = bool(p2)
        toms[0]["hash"] = keep
    else:
        chk["tampered receipt detected"] = False

    # 9. it survives a reload from disk
    m2 = Mnemo(path=str(d / "store.jsonl"), receipts=True)
    blob2 = " ".join((r.get("text") or "") for r in m2.items).lower()
    chk["still erased after reload"] = not any(s in blob2 for s in secrets)

    h = state_hash(m2)
    shutil.rmtree(d, ignore_errors=True)
    return chk, ev, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--version", default=None)
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gov_audit_"))
    if a.local:
        pkg, src, sha = pathlib.Path(__file__).resolve().parent, "working tree", "n/a"
    else:
        subprocess.run([sys.executable, "-m", "pip", "download",
                        f"agora-mnemo=={a.version}" if a.version else "agora-mnemo",
                        "--no-deps", "-d", str(tmp)], capture_output=True, check=True)
        wheel = sorted(tmp.glob("*.whl"))[0]
        pkg = tmp / "pkg"
        zipfile.ZipFile(wheel).extractall(pkg)
        src, sha = wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest()
    os.environ[PKG_ENV] = str(pkg)
    sys.path.insert(0, str(pkg))
    import inspeximus as mnemo

    print("=" * 96)
    print(f"auditing : {src}   version {getattr(mnemo, '__version__', '?')}")
    if sha != "n/a":
        print(f"sha256   : {sha}")
    print(f"claim    : \"tell it to forget everything about a subject, and it can prove it\"")
    print(f"design   : {len(SCENARIOS)} scenarios x {a.repeats} repeats, every check must hold every time")
    print("=" * 96)

    total_fail = 0
    for sc in SCENARIOS:
        print(f"\n--- {sc['name']}  (subject: {sc['subject']})")
        agg, hashes = {}, []
        for run in range(a.repeats):
            chk, ev, h = run_scenario(sc, run)
            hashes.append(h)
            for k, ok in chk.items():
                agg.setdefault(k, []).append(ok)
            if run == 0:
                for line in ev:
                    print(f"      {line}")
        for k, oks in agg.items():
            tag = "PASS" if all(oks) else "FAIL"
            total_fail += not all(oks)
            print(f"  [{tag}] {k}  ({sum(oks)}/{len(oks)} runs)")
        same = len(set(hashes)) == 1
        total_fail += not same
        print(f"  [{'PASS' if same else 'FAIL'}] identical end state across {a.repeats} runs "
              f"({hashes[0][:16]}...)")

    print("\n" + "=" * 96)
    print("CLAIM HOLDS" if not total_fail else f"CLAIM BROKEN — {total_fail} failing checks")
    print("=" * 96)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
