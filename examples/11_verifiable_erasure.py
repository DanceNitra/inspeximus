"""
inspeximus example 11 — verifiable erasure in three commands, with the control that makes it mean something.

    pip install inspeximus cryptography
    python 11_verifiable_erasure.py          # exits non-zero if any assertion below stops holding

A delete that returns success tells you the call ran. It does not tell you the data left. This script
walks the path in docs/ERASURE.md end to end, using the REAL command-line program in a throwaway
directory, and checks every claim the documentation makes:

  1. delete      inspeximus forget-subject
  2. certificate inspeximus erasure-certificate     (signed, content-free, independently verifiable)
  3. residue     inspeximus residue                 (vendor-neutral byte scan of the whole directory)

THE CONTROL IS THE POINT. A store that had silently wiped everything would pass step 3 perfectly, so a
clean scan is evidence only alongside a scan that still FINDS something. Both halves are asserted here:
the deleted subject is gone AND the neighbour is still present, in the same directory, on the same
command. Likewise the certificate is checked in both directions — an honest one verifies, a tampered
one FAILS — because a verifier that cannot fail has measured nothing.

HONEST SCOPE, in full in docs/ERASURE.md and repeated where it applies below:
  * this checks LOGICAL residue, not at-rest security. A plaintext store of any library also leaves
    bytes in free space, on over-provisioned SSD blocks, in snapshots and in backups; the defence there
    is full-disk encryption plus crypto-erasure, and nothing that reads files can judge that layer.
  * it matches LITERAL BYTES. A stored value is caught, a paraphrase is not, and neither is a
    lowercased or base64 copy. A clean result is evidence, not proof.
  * pointed at another system, a PRESENT hit in an audit log may be a deliberate design choice
    (retaining a deletion record is often required), not a defect.

No LLM, no embedder, no network. Ed25519 signing needs `cryptography`; without it the script still runs
and reports the tombstones as UNSIGNED rather than pretending otherwise.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def run(*args, env=None, cwd=None):
    """One `inspeximus ...` invocation. Returns (exit_code, merged stdout+stderr).

    `-m inspeximus.cli` rather than the console script so this runs from a source checkout with no
    install step; the two are the same program.
    """
    proc = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                          capture_output=True, text=True, env=env, cwd=cwd)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def show(label, code, out):
    print(f"\n$ inspeximus {label}")
    for line in out.rstrip("\n").splitlines():
        print(f"  {line}")
    print(f"  [exit {code}]")


def check(claim: str, condition: bool) -> None:
    """Assert and SAY SO. A silent pass and a skipped check look identical from the outside."""
    print(("  PASS  " if condition else "  FAIL  ") + claim)
    if not condition:
        raise SystemExit(f"\nASSERTION FAILED: {claim}")


def main() -> int:
    work = tempfile.mkdtemp(prefix="inspeximus-erasure-")
    data = os.path.join(work, "dsar")
    os.makedirs(data, exist_ok=True)
    store = os.path.join(data, "store.json")

    env = dict(os.environ)
    # Run against THIS checkout, not whatever `inspeximus` happens to be installed. A demo that
    # silently measures a different build than the one it ships beside proves nothing about this one.
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)         # never touch the caller's real store

    print(__doc__.split("HONEST SCOPE")[0].strip())
    print("\n" + "=" * 78)
    print(f"working directory: {work}")

    # --- the signing key is YOURS -------------------------------------------------------------
    # The secret half signs the tombstones and must be present when the ERASURE runs, not when the
    # certificate is printed: tombstones are signed as they are created. It goes in a FILE, never on a
    # command line, where it would land in `ps`, shell history and CI logs.
    signed_path = True
    try:
        from inspeximus import new_receipt_keypair
        sk, pk = new_receipt_keypair()
        with open(os.path.join(work, "receipt.key"), "w", encoding="utf-8") as fh:
            fh.write(sk)
        with open(os.path.join(work, "receipt.key.pub"), "w", encoding="utf-8") as fh:
            fh.write(pk)
        env["INSPEXIMUS_RECEIPT_KEY_FILE"] = os.path.join(work, "receipt.key")
        print(f"receipt public key: {pk}")
    except RuntimeError as e:                # cryptography not installed
        signed_path, pk = False, None
        print(f"NOTE: signing unavailable ({e}). The chain still proves integrity, not authorship, "
              f"and the verifier will report signatures_valid: n/a rather than OK.")

    # --- four records about two people --------------------------------------------------------
    print("\n--- setup: four records, two data subjects " + "-" * 34)
    code, out = run("--path", store, "--json", "remember",
                    "Alice Novak, alice@example.com, lives in Frankfurt",
                    "--key", "alice::contact", "--object", "Frankfurt",
                    "--source", "alice@example.com", env=env)
    alice_id = json.loads(out)["id"]
    print(f"  alice record        {alice_id}")

    # A CORRECTION. The pre-correction value survives as a superseded row -- history a DSAR must still
    # reach, and a store that erased only the ACTIVE row would leak exactly here.
    run("--path", store, "remember", "correction: Alice Novak relocated to Ohio",
        "--key", "alice::contact", "--object", "Ohio", "--source", "alice@example.com", env=env)

    # A DERIVED summary: it names neither Alice nor her address, and a text-match delete misses it.
    # --derived-from is what makes it reachable, and it must resolve -- see the dangling-id check below.
    code, out = run("--path", store, "--json", "remember",
                    "summary: the Frankfurt hire is on the priority tier",
                    "--source", "analytics.internal", "--derived-from", alice_id, env=env)
    print(f"  derived summary     {json.loads(out)['id']}  (derived_from {alice_id})")

    run("--path", store, "remember", "Bob Weber, bob@example.com, lives in Munich",
        "--key", "bob::contact", "--source", "bob@example.com", env=env)
    print("  neighbour           Bob Weber (the control -- he must SURVIVE)")

    # A mistyped parent id is the quietest way to lose a DSAR: the lineage claim does not land, the
    # record inherits no taint, and forget-subject cannot reach it. The surface must say so.
    code, out = run("--path", store, "remember", "summary: an unrelated note",
                    "--source", "analytics.internal", "--derived-from", "0000000000", env=env)
    check("a dangling --derived-from id is reported, not silently dropped",
          "do not exist in this store" in out)
    stray = json.loads(run("--path", store, "--json", "list", "-n", "1", env=env)[1])[0]["id"]
    run("--path", store, "forget", "--id", stray, env=env)

    # === 1. DELETE ============================================================================
    print("\n--- 1. delete " + "-" * 63)
    code, out = run("--path", store, "forget-subject", "alice@example.com",
                    "--request-id", "DSAR-2026-014", "--basis", "GDPR Art.17", env=env)
    show("forget-subject alice@example.com --request-id DSAR-2026-014 --basis 'GDPR Art.17'", code, out)
    check("the erasure reached 3 records: the subject's own, the value a correction "
          "retired, and the summary derived from her", "erased 3 record(s)" in out)

    # === 2. CERTIFICATE =======================================================================
    print("\n--- 2. get a signed certificate " + "-" * 45)
    cert_path = os.path.join(work, "cert.json")
    code, out = run("--path", store, "erasure-certificate", "--request-id", "DSAR-2026-014",
                    "--out", cert_path, env=env)
    show("erasure-certificate --request-id DSAR-2026-014 --out cert.json", code, out)
    check("the certificate attests 3 erasures and the command succeeds",
          code == 0 and "3 erasure(s) attested" in out)

    with open(cert_path, encoding="utf-8") as fh:
        cert = json.load(fh)
    check("the certificate is CONTENT-FREE: no erased text appears anywhere in it",
          not any(s in json.dumps(cert) for s in ("Frankfurt", "Ohio", "Alice Novak",
                                                  "alice@example.com")))
    if signed_path:
        check("every tombstone is Ed25519-signed",
              bool(cert["tombstones"]) and all("sig" in t for t in cert["tombstones"]))

    # === 3. RESIDUE ===========================================================================
    print("\n--- 3. verify the residue " + "-" * 51)
    code_gone, out_gone = run("residue", "--root", data, "--value", "alice@example.com", env=env)
    show("residue --root ./dsar --value alice@example.com", code_gone, out_gone)

    # THE CONTROL, and it is the whole point. A store that had silently wiped everything would score a
    # perfect pass on the line above. The same scan, same directory, for the OTHER person must still
    # FIND the value -- otherwise "clean" is a silence, not a measurement.
    print("\n--- the control: the scanner must still be able to find something " + "-" * 11)
    code_here, out_here = run("residue", "--root", data, "--value", "bob@example.com", env=env)
    show("residue --root ./dsar --value bob@example.com", code_here, out_here)

    check("HALF 1 - the erased subject is GONE from every file in the directory",
          code_gone == 0 and "clean - no residue found" in out_gone)
    check("HALF 2 - the neighbour is STILL PRESENT, so the scan can detect presence at all",
          code_here == 1 and "PLAIN" in out_here and "residue found" in out_here)

    code, out = run("--path", store, "list", "-n", "5", env=env)
    check("HALF 2 - and the neighbour's memory still answers",
          "Bob Weber" in out and "Alice Novak" not in out)

    # Every value the erasure touched, including the one a correction had already retired.
    for value in ("Alice Novak", "Frankfurt", "Ohio", "alice@example.com"):
        c, _ = run("residue", "--root", data, "--value", value, env=env)
        check(f"no residue for {value!r} (a superseded value is erased too)", c == 0)

    # === THE AUDITOR'S SIDE ====================================================================
    print("\n--- what the auditor runs (no private key, no trust in the operator) " + "-" * 9)
    verify_args = ["erasure-verify", cert_path, "--store", store]
    if signed_path:
        verify_args += ["--expected-pubkey-file", os.path.join(work, "receipt.key.pub")]
    code, out = run(*verify_args, env=env)
    show("erasure-verify cert.json --store ./dsar/store.json --expected-pubkey-file receipt.key.pub",
         code, out)
    check("an honest certificate VERIFIES, with the store-absence proof performed",
          code == 0 and "VERDICT: PASS" in out and "OK   store_absent" in out)

    # A verifier that cannot fail has measured nothing. The scope statement is the sentence a regulator
    # most needs -- "NOT a compliance certification" -- and it was once free text nobody compared.
    print("\n--- and it must FAIL on a tampered certificate " + "-" * 30)
    tampered_path = os.path.join(work, "cert-tampered.json")
    tampered = json.loads(json.dumps(cert))
    tampered["scope"] = "Full GDPR compliance certification, all systems."
    with open(tampered_path, "w", encoding="utf-8") as fh:
        json.dump(tampered, fh, ensure_ascii=False, indent=2)
    code, out = run(*(["erasure-verify", tampered_path, "--store", store] + verify_args[4:]), env=env)
    show("erasure-verify cert-tampered.json --store ./dsar/store.json", code, out)
    check("a rewritten scope statement is REJECTED",
          code == 1 and "FAIL scope_intact" in out and "VERDICT: FAIL" in out)

    for label, mutate in (
        ("a forged erased-id list", lambda c: c.update(erased_memory_ids=["0000000000"], count=1)),
        ("an edited tombstone timestamp", lambda c: c["tombstones"][0].update(ts=0.0)),
        ("a dropped tombstone", lambda c: c.update(tombstones=c["tombstones"][1:])),
    ):
        bad = json.loads(json.dumps(cert))
        mutate(bad)
        bad_path = os.path.join(work, "cert-bad.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh, ensure_ascii=False, indent=2)
        code, _ = run(*(["erasure-verify", bad_path, "--store", store] + verify_args[4:]), env=env)
        check(f"{label} is REJECTED", code == 1)

    # A certificate for a request that never ran certifies nothing, and every consistency check in it
    # passes vacuously. It used to verify `valid: true`; it is now refused at BOTH ends, by the producer
    # and by the verifier -- see the defect section in docs/ERASURE.md.
    print("\n--- a certificate that attests to nothing " + "-" * 35)
    never = os.path.join(work, "cert-never.json")
    code, out = run("--path", store, "erasure-certificate", "--request-id", "DSAR-2026-999",
                    "--out", never, env=env)
    show("erasure-certificate --request-id DSAR-2026-999 --out cert-never.json", code, out)
    check("the PRODUCER refuses a zero-erasure certificate as evidence",
          code == 1 and "ZERO erasures" in out)
    code, out = run(*(["erasure-verify", never, "--store", store] + verify_args[4:]), env=env)
    check("and the VERIFIER fails it (attests_an_erasure)",
          code == 1 and "FAIL attests_an_erasure" in out)

    # === WHAT THIS DID NOT PROVE ===============================================================
    print("\n" + "=" * 78)
    print("""ALL CHECKS PASSED. What that does and does not mean:

  It DOES mean the records attributable to alice@example.com -- her own, the value her correction
  retired, and the summary derived from her -- are absent from the raw bytes of every file in that
  directory, that the act is recorded in a signed content-free chain that re-derives from genesis,
  and that the receipt fails if anyone edits it.

  It does NOT mean the data is unrecoverable. Not checked, and not checkable by reading files:
    * at-rest bytes -- filesystem free space, over-provisioned SSD blocks, snapshots, backups.
      Use full-disk encryption plus crypto-erasure (destroy the key, not the row).
    * stores nobody pointed it at -- your vector index, prompt and retrieval logs, caches, an LLM
      provider's request logs. Register them with register_erasure_target() for a DeletionManifest
      that NAMES any store still leaking.
    * paraphrases and re-encodings of the value; the match is literal bytes.
    * text reconstructible from RETAINED embeddings (Morris et al., EMNLP 2023).

  These are integrity primitives that produce evidence. They are not a compliance certification --
  and the certificate says so in a field the verifier checks.""")
    print(f"\n(working directory left for inspection: {work})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
