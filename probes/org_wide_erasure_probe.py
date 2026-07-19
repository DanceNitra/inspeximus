"""Org-wide erasure receipt (the real moat / PRO direction) — DeletionManifest cascades a right-to-erasure
across EVERY registered store (mnemo + the app's vector store + logs + backups), verifies each reports the
subject unrecoverable, and emits ONE signed, tamper-evident manifest. The point a within-one-library scrub
cannot make: if a store DID NOT comply, the receipt NAMES it (residual_targets) instead of hiding it.

Demonstrates: (A) full cascade -> complete=True, verify ok; (B) a non-compliant backup -> complete=False, the
receipt names the leaker (does NOT falsely certify); (C) tamper an entry -> verify catches it.
"""
import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo, new_receipt_keypair
from mnemo.deletion_manifest import DeletionManifest, ErasureTarget

SEC = "Alice-SSN-441-90-2277"


class MnemoTarget(ErasureTarget):
    name = "mnemo-store"
    def __init__(self, m): self.m = m
    def erase(self, subject):
        r = self.m.forget_subject(subject, request_id="dsr"); self.m._save(force=True)
        return {"erased": r.get("erased", r.get("forgotten", 0))}
    def still_recoverable(self, subject, values):
        blob = " ".join((x.get("text") or "") for x in getattr(self.m, "items", []))
        return any(v in blob for v in values)


class DictStore(ErasureTarget):
    """A mock app-side store (vector index / retrieval log). compliant=False simulates a store that ignores delete."""
    def __init__(self, name, compliant=True): self.name = name; self.compliant = compliant; self.rows = {}
    def add(self, k, text): self.rows[k] = text
    def erase(self, subject):
        if not self.compliant:
            return {"erased": 0}                       # ignores the delete (the non-compliant store)
        n = len([k for k, t in self.rows.items() if subject.lower() in t.lower()])
        self.rows = {k: t for k, t in self.rows.items() if subject.lower() not in t.lower()}
        return {"erased": n}
    def still_recoverable(self, subject, values):
        blob = " ".join(self.rows.values())
        return any(v in blob for v in values)


def run():
    ok = {}
    sk, pub = new_receipt_keypair()

    def build(backup_compliant):
        d = os.path.join(tempfile.mkdtemp(), "m.json")
        m = Mnemo(path=d); m.remember(SEC, key="alice::ssn", source={"doc": "alice"}, pii=True); m._save(force=True)
        vec = DictStore("app-vector-index"); vec.add("v1", "profile: " + SEC)
        log = DictStore("retrieval-log"); log.add("l1", "query hit -> " + SEC)
        bak = DictStore("nightly-backup", compliant=backup_compliant); bak.add("b1", "snapshot " + SEC)
        man = DeletionManifest(sign_sk_hex=sk, pubkey_hex=pub)
        man.register(MnemoTarget(m)).register(vec).register(log).register(bak)
        return man

    # A) full cascade — every store complies
    man = build(backup_compliant=True)
    cert = man.execute("alice", [SEC], request_id="dsr-A")
    ok["A cascaded all 4 stores"] = len(cert["entries"]) == 4
    ok["A complete=True (all verified absent)"] = cert["complete"] is True
    ok["A no residual targets"] = cert["residual_targets"] == []
    ok["A verify() ok"] = man.verify(cert)[0] is True
    ok["A signed"] = all("sig" in e for e in cert["entries"])

    # B) a non-compliant backup — the receipt must NAME it, not falsely certify
    man2 = build(backup_compliant=False)
    cert2 = man2.execute("alice", [SEC], request_id="dsr-B")
    ok["B complete=False (a store didn't comply)"] = cert2["complete"] is False
    ok["B receipt NAMES the leaker"] = cert2["residual_targets"] == ["nightly-backup"]
    ok["B other 3 stores verified absent"] = sum(e["verified_absent"] for e in cert2["entries"]) == 3
    ok["B verify() still ok (honest record, not hidden)"] = man2.verify(cert2)[0] is True

    # C) tamper an entry -> verify catches it
    import copy
    bad = copy.deepcopy(cert)
    bad["entries"][0]["still_recoverable"] = False if bad["entries"][0]["still_recoverable"] else True
    ok["C tampered manifest -> verify FAILS"] = man.verify(bad)[0] is False

    print("=" * 60)
    print("Org-wide erasure receipt — cascade + verify + catch non-compliance")
    print("=" * 60)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    bad_ = [k for k, v in ok.items() if not v]
    print("-" * 60)
    print("RECEIPT:", "VALID - all checks hold" if not bad_ else f"FAIL: {bad_}")
    return 0 if not bad_ else 1


if __name__ == "__main__":
    raise SystemExit(run())
