"""
Runnable probe for mnemo 0.5.2 -- distinct-VERIFIED-KEY corroboration (strict_corroboration).

THE GAP IT CLOSES. mnemo's corroboration gate (episodic->semantic graduation + recall(influence_only))
counts ">=2 distinct sources". By default a source is a canonical STRING (entity-resolved), which
collapses honest sybil variants ("Wikipedia"/"wikipedia.org"/URL) but is still SPOOFABLE: an attacker who
controls the labeling channel can supply two unrelated source strings it owns and manufacture "independent"
corroboration. strict_corroboration binds the independence rail to an ORIGIN-SIGNED rail: a corroborating
link counts only if it carries a VERIFIED KEY (remember(..., attestation=...)), so N sybil variants of one
origin collapse to one witness unless the attacker holds N distinct Ed25519 keys (a costly identity;
Douceur 2002). This does NOT make a claim TRUE -- an attested source can still sign a false claim
(wrong-at-write-time / MINJA survives a signature); it makes manufactured independence expensive and a
caught liar a non-repudiable, revocable key.

Scenarios (all asserted below):
  1. STRING-SPOOF poison: one attacker, two DIFFERENT source strings it controls -> passes the DEFAULT
     (string) gate, FAILS the strict (verified-key) gate.
  2. TWO REAL SIGNED WITNESSES: two distinct source keys attest -> passes strict (genuine independence).
  3. FORGED attestation is REJECTED at write time (loud, not silently dropped).
  4. SIGNATURE IS CLAIM-BOUND: a valid signature for claim X cannot be replayed onto claim Y.

Needs `cryptography` (pip install cryptography). No cloud, no data files.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mnemo as M


def fresh():
    m = M.Mnemo()
    return m


def link(m, target_id, corroborator_id):
    """Register corroborator as a supporting link on target (how the gate counts independence)."""
    for r in m.items:
        if r["id"] == target_id:
            r.setdefault("links", []).append(corroborator_id)


print("mnemo", M.__version__)
sk_a, pk_a = M.new_source_keypair()      # attacker's single key
sk_b, pk_b = M.new_source_keypair()      # a genuinely independent witness
CLAIM = "the deploy key rotates every 24h"

# ---------------------------------------------------------------- Scenario 1: string-spoof
m = fresh()
tgt = m.remember(CLAIM, source={"doc": "attacker-blog"})
# attacker mints TWO 'independent' corroborators under DIFFERENT strings it controls, NO keys
c1 = m.remember(CLAIM, source={"doc": "totally-different-site"})
c2 = m.remember(CLAIM, source={"doc": "another-unrelated-name"})
link(m, tgt, c1); link(m, tgt, c2)
byid = {r["id"]: r for r in m.items}
tgt_rec = byid[tgt]
default_ok = M.Mnemo._is_corroborated(tgt_rec, byid, strict=False)
strict_ok = M.Mnemo._is_corroborated(tgt_rec, byid, strict=True)
print("\n[1] string-spoof (2 attacker-owned strings, 0 keys):")
print(f"    default(string) corroborated = {default_ok}   strict(verified-key) corroborated = {strict_ok}")
assert default_ok is True, "string gate should be fooled by 2 distinct strings"
assert strict_ok is False, "verified-key gate must REJECT 0-key corroboration"

# ---------------------------------------------------------------- Scenario 2: two real signed witnesses
m = fresh()
tgt = m.remember(CLAIM, source={"doc": "primary"})
w1 = m.remember(CLAIM, source={"doc": "witness-a"},
                attestation=(pk_a, M.attest(CLAIM, sk_a, "witness-a")))
w2 = m.remember(CLAIM, source={"doc": "witness-b"},
                attestation=(pk_b, M.attest(CLAIM, sk_b, "witness-b")))
link(m, tgt, w1); link(m, tgt, w2)
byid = {r["id"]: r for r in m.items}
strict_ok = M.Mnemo._is_corroborated(byid[tgt], byid, strict=True)
nkeys = M.Mnemo._distinct_verified_keys(byid[tgt].get("links"), byid)
print("\n[2] two DISTINCT signed witnesses:")
print(f"    distinct verified keys = {nkeys}   strict corroborated = {strict_ok}")
assert nkeys == 2 and strict_ok is True, "two distinct verified keys must pass strict"

# sybil check: the SAME key attesting twice is still one witness
m = fresh()
tgt = m.remember(CLAIM, source={"doc": "primary"})
s1 = m.remember(CLAIM, source={"doc": "name-x"}, attestation=(pk_a, M.attest(CLAIM, sk_a, "name-x")))
s2 = m.remember(CLAIM, source={"doc": "name-y"}, attestation=(pk_a, M.attest(CLAIM, sk_a, "name-y")))
link(m, tgt, s1); link(m, tgt, s2)
byid = {r["id"]: r for r in m.items}
nkeys = M.Mnemo._distinct_verified_keys(byid[tgt].get("links"), byid)
print(f"    same key twice -> distinct verified keys = {nkeys} (sybil collapses)")
assert nkeys == 1, "one key used twice must collapse to one witness"

# ---------------------------------------------------------------- Scenario 3: forged attestation rejected
m = fresh()
rejected = False
try:
    m.remember(CLAIM, source={"doc": "witness-a"},
               attestation=(pk_a, "00" * 64))          # a bogus signature
except ValueError:
    rejected = True
print("\n[3] forged attestation rejected at write time:", rejected)
assert rejected is True, "a bad signature must raise, not be silently stored"

# ---------------------------------------------------------------- Scenario 4: signature is claim-bound
m = fresh()
replayed = False
sig_for_x = M.attest(CLAIM, sk_a, "witness-a")
try:
    m.remember("a DIFFERENT claim the attacker wants to smuggle", source={"doc": "witness-a"},
               attestation=(pk_a, sig_for_x))          # replay X's signature onto Y
except ValueError:
    replayed = True
print("[4] signature for claim X cannot be replayed onto claim Y:", replayed)
assert replayed is True, "a claim-bound signature must not verify for a different claim"

print("\nALL ASSERTIONS PASS -- strict_corroboration counts distinct VERIFIED KEYS: a string-spoof that")
print("fools the default gate is rejected, real distinct-key witnesses pass, forged/replayed attestations")
print("are refused at write time. Honest limit: this buys unforgeable INDEPENDENCE, not correctness --")
print("an attested source can still sign a false claim (MINJA); what it costs the attacker is real keys.")
