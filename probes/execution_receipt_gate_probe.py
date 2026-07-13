"""
execution_receipt_gate_probe.py -- seal the "fabricated tool-call log" hole, don't just name it.  MIT.

The hole (raised on DeepSeek-V3 #1462 by @icophy for a "No Execution, No Memory" design): a memory is
allowed in only after a tool call -- but a session can WRITE a fabricated tool-call LOG ("I called
search('X') and it returned Y") that looks identical to a real one. The provenance-of-execution is
self-asserted content, so a fabricated execution earns memory exactly like a real one (the ~0.9 attack
success we measured for content-declared provenance across 10 models).

This is textbook signed-execution/attestation (code signing, RFC 9334 RATS remote attestation, SLSA
provenance) WIRED to memory-write standing -- the contribution is the wiring + a runnable receipt, not
the idea. It closes LAYER 1 only (provenance: a call HAPPENED), not LAYER 2 (veracity: whether a real
call's OUTPUT is honest). Signing authenticates the SOURCE, not the TRUTH.

The plug (this probe, runnable): bind a memory's EXECUTION STANDING to a RUNTIME-signed receipt, not the
session's word. LOAD-BEARING ASSUMPTION: the tool executor is a trust domain SEPARATE from the session --
an out-of-process runtime / MCP server / sandbox holding the Ed25519 key rt_sk unreadable by the session,
signing only results it actually produced. Given that, a real call produces a receipt signed over the
actual (tool, result); a memory claiming a tool result earns standing ONLY if attested by an ALLOWLISTED
executor key. A fabricated log has no runtime signature and cannot be forged into it (Ed25519). If the
executor shares a process/trust domain with the session, rt_sk is session-readable and the plug is THEATER.

Cost (the honest requirement): a small allowlist of trusted executor keys -- ONE key per tool-runtime,
an exogenous trust root. That is cheap (unlike per-fact attestation) and it is the thing that converts
"who wrote this" from a free string into a runtime signature.

Honest boundary (this RAISES/BOUNDS, it does not close poisoning): it seals FABRICATION (a log of a call
that never happened). It does NOT stop (a) a compromised runtime, (b) a session that holds the runtime
key, or (c) a REAL call that returns attacker-influenced data (a poisoned tool honestly signs a bad
result -- MINJA arXiv:2503.03704). That residual is bounded elsewhere (earned-outcome standing + a
reversibility checkpoint on the first irreversible use), not here.

Run:  python execution_receipt_gate_probe.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo, new_source_keypair, attest


def _store():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(p)
    try:
        return Mnemo(path=p)
    except TypeError:
        return Mnemo()


def main():
    m = _store()

    # --- exogenous trust root: the tool RUNTIME's key (held by the executor, NOT the session) ---
    rt_sk, rt_pk = new_source_keypair()
    EXECUTOR_ALLOWLIST = {rt_pk}          # one key per trusted runtime; the whole cost of the plug

    def earns_execution_standing(mid):
        """A memory claiming a tool result acts only if attested by an ALLOWLISTED executor key."""
        rec = next(r for r in m.items if r["id"] == mid)
        return rec.get("attested_key") in EXECUTOR_ALLOWLIST

    N = 200

    # --- BASELINE: trust the content log (icophy's current rail) -> a fabricated log is believed ---
    baseline_served = 0
    for i in range(N):
        # attacker writes a fabricated tool-call log; nothing checks it, so it "counts"
        mid = m.remember("tool=search result=ATTACKER_VALUE_%d (fabricated log)" % i,
                         mtype="episodic", source={"doc": "session"})
        baseline_served += 1     # the log field is simply believed -> served
    print(f"BASELINE (trust the tool-call log): fabricated logs served = {baseline_served}/{N}  (ASR 1.00)")

    # --- PLUG: execution standing requires an allowlisted runtime signature ---
    # honest path: the runtime actually ran the tool and SIGNS the real result
    real_result = "tool=search result=REAL_VALUE"
    sig = attest(real_result, rt_sk, "runtime:search")
    honest_mid = m.remember(real_result, mtype="episodic",
                            source={"doc": "runtime:search"}, attestation=(rt_pk, sig))
    honest_ok = earns_execution_standing(honest_mid)

    # attacker path (a): fabricate a log with NO runtime signature
    fab_served = 0
    for i in range(N):
        mid = m.remember("tool=search result=ATTACKER_VALUE_%d" % i,
                         mtype="episodic", source={"doc": "session"})   # no attestation
        if earns_execution_standing(mid):
            fab_served += 1

    # attacker path (b): session MINTS ITS OWN key and signs -- but it is not on the executor allowlist
    ss_sk, ss_pk = new_source_keypair()
    self_served = 0
    for i in range(N):
        txt = "tool=search result=ATTACKER_SIGNED_%d" % i
        s = attest(txt, ss_sk, "session")
        mid = m.remember(txt, mtype="episodic", source={"doc": "session"}, attestation=(ss_pk, s))
        if earns_execution_standing(mid):
            self_served += 1

    # attacker path (c): EXERCISE THE CRYPTO -- attacker knows the runtime PUBLIC key rt_pk and tries to
    # claim it without holding rt_sk (forge a signature). mnemo verifies the signature and must REJECT the
    # write. This is the one cell that leans on Ed25519 unforgeability rather than allowlist membership.
    forgeries_rejected = 0
    for i in range(N):
        txt = "tool=search result=FORGED_UNDER_RUNTIME_KEY_%d" % i
        bogus_sig = "00" * 64                      # a signature the attacker cannot actually produce
        try:
            m.remember(txt, mtype="episodic", source={"doc": "runtime:search"}, attestation=(rt_pk, bogus_sig))
        except ValueError:
            forgeries_rejected += 1                # remember() rejects: signature does not verify for rt_pk

    print(f"PLUG  honest runtime-signed result earns execution standing? {honest_ok}")
    print(f"PLUG  [a] fabricated log, no runtime sig        served = {fab_served}/{N}   (ASR {fab_served/N:.2f})  <- allowlist (tautological cell)")
    print(f"PLUG  [b] session mints its own key + signs     served = {self_served}/{N}   (ASR {self_served/N:.2f})  <- allowlist (tautological cell)")
    print(f"PLUG  [c] attacker claims rt_pk w/o rt_sk (forge) rejected = {forgeries_rejected}/{N}          <- Ed25519 unforgeability (the real property)")
    print()
    print("HONEST READ (by construction, GIVEN the load-bearing assumption): if the tool-executor is a trust")
    print("  domain SEPARATE from the session -- an out-of-process runtime / MCP server / sandbox holding rt_sk")
    print("  unreadable by the session, signing only results IT produced -- then a fabricated tool-call LOG earns")
    print("  0 execution-standing (cells a/b) and cannot be forged into (cell c). Cells a/b are allowlist")
    print("  membership (entailed); cell c is the crypto. This is textbook signed-execution/attestation (code")
    print("  signing, RFC 9334 RATS remote attestation, SLSA provenance) WIRED to memory-write standing -- the")
    print("  contribution is the wiring + this receipt, not the idea. If executor and session share a process,")
    print("  rt_sk is session-readable and the plug is THEATER (path b collapses to a valid runtime signature).")
    print("SCOPE: this closes LAYER 1 only -- provenance that a call HAPPENED. It does NOT close LAYER 2 -- the")
    print("  VERACITY of a real call's output: a genuinely-executed poisoned tool (MINJA 2503.03704 / PoisonedRAG")
    print("  2402.07867) is signed honestly by the runtime and keeps full standing. Signing authenticates the")
    print("  SOURCE, not the TRUTH. Layer 2 needs earned-outcome standing + a reversibility checkpoint, not this.")


if __name__ == "__main__":
    main()
