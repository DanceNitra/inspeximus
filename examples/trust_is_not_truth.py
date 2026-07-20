"""Source trust is an authorization control, not a correctness one — run it and see.

    pip install agora-mnemo
    python trust_is_not_truth.py

Standalone on purpose: no repo checkout, no path juggling, one dependency that installs from PyPI.

Why it exists. mnemo ships a provenance gate: writes can be signed with an Ed25519 source key, a trust
root is configured once, and `recall(trusted_only=True)` returns only what chains to that root. On the
usual poisoning fixture it scores perfectly — and that score is close to meaningless, because in those
fixtures every TRUE fact is signed by the trusted key and every POISONED one by the attacker. Truth and
trust are entangled by construction, so a filter on the key looks like a detector of truth.

This decouples them, and the result is the honest scope of any such gate — ours included, and by
extension any scheme keyed on a source LABEL, which is strictly weaker than a signature:

  Cell A  a TRUSTED key signs a WRONG fact, the correct fact is unsigned  -> the wrong fact is returned
  Cell B  the correct fact is signed by an UNKNOWN key, nothing trusted   -> nothing is returned

The claim a provenance gate earns is "rejects writes from untrusted sources". It never earns "returns
the true value". A mistaken or compromised high-trust source is precisely the case it cannot see.
"""
from mnemo import Mnemo, attest, new_source_keypair

TRUSTED_SK, TRUSTED_PK = new_source_keypair()      # the configured trust root
UNKNOWN_SK, UNKNOWN_PK = new_source_keypair()      # some source nobody vouched for

QUESTION = "which bank should be used for my transfer?"
CORRECT = "My bank is Nordstar Credit Union."
WRONG = "My bank is Zephyr Trust."


def store():
    m = Mnemo(path=None)
    m.strict_corroboration = True
    m.trust_seeds = {"key:" + TRUSTED_PK}
    return m


# --- Cell A: the trusted source is simply wrong, and the correct fact carries no signature
a = store()
a.remember(WRONG, key="bank", attestation=(TRUSTED_PK, attest(WRONG, TRUSTED_SK)))
a.remember(CORRECT, key="bank_unsigned")
hits_a = a.recall(QUESTION, k=3, trusted_only=True)
top_a = hits_a[0]["text"] if hits_a else "(nothing returned)"

# --- Cell B: the correct fact is signed, but by a key the trust root does not vouch for
b = store()
b.remember(CORRECT, key="bank", attestation=(UNKNOWN_PK, attest(CORRECT, UNKNOWN_SK)))
hits_b = b.recall(QUESTION, k=3, trusted_only=True)
top_b = hits_b[0]["text"] if hits_b else "(nothing returned)"

print(f"correct answer : {CORRECT}\n")
print(f"Cell A  trusted-but-wrong present, correct fact unsigned")
print(f"        trusted_only -> {top_a}")
print(f"        {'the WRONG fact, at full weight' if top_a == WRONG else 'UNEXPECTED — please open an issue'}\n")
print(f"Cell B  correct fact signed by an unknown key")
print(f"        trusted_only -> {top_b}")
print(f"        {'the correct fact is dropped' if top_b.startswith('(nothing') else 'UNEXPECTED — please open an issue'}\n")
print("A provenance gate answers WHO WROTE THIS, never IS THIS TRUE. Corroboration — whether anything")
print("independent confirms the claim — is a separate axis, and it is the one that degrades gracefully")
print("when a trusted source turns out to be wrong.")
