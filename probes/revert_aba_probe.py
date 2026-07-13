"""revert_aba_probe.py — the ABA question (jacksonxly, r/RAG 2026-07-12): does submit_revert key on the
IDENTITY of what it retires, or on current content?

His case: a value is asserted, reverted, then re-asserted down a different write path so it reads identical.
A revert/restore that resolves on CONTENT can then land against the wrong assertion. If the intent carries
the id of what it supersedes, it is immune.

Measured on the real store, both in-stream paths:

  RELATIVE (revert_intent): base = current_active_id, target = the predecessor via superseded_by_toggle ==
    cur.id. Both ends are IDs. Under ABA (state moves to a same-value record) the base no longer matches ->
    conflict; it never lands against a re-asserted look-alike. IMMUNE.

  ABSOLUTE (restore_intent): 0.7.14 carried only a value token ("restore:key=VALUE#nonce") and resolved
    `VALUE in chain` -> content. 0.7.15 also carries the id of the specific record that held the value at
    mint ("restore:key=VALUE@ID#nonce") and requires THAT record to still exist with that value. A same-value
    re-assertion down another path is a different id and cannot satisfy it. The channel still owes the land
    (unconditional by design), but it lands a specific INSTANCE, not "the current value that looks like X".

Deterministic, no LLM, no network. RUN: python mnemo/probes/revert_aba_probe.py
"""
import sys, pathlib, json, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo, new_receipt_keypair, sign_revert, __version__

sk, pk = new_receipt_keypair()
signer = lambda i: sign_revert(sk, i)
R = {"mnemo_version": __version__}


def cur(m, k="region"):
    a = [r for r in m.items if r.get("key") == k and r.get("status") == "active" and r.get("object")]
    return max(a, key=lambda r: r.get("valid_from", r["ts"]))["object"] if a else None


# ── RELATIVE under ABA — base binds to the active id, target to the id-linked predecessor. ─────
# echo_guard OFF so the re-assertion of A is a live new record (a genuine ABA state move), which is the
# adversarial case: the base id no longer matches -> conflict, never landing against the look-alike.
m = Mnemo(path=None, revert_pubkey=pk); m.echo_guard = False
m.remember("region is A", key="region", object="A")                    # rec1
m.remember("correction: region is now B", key="region", object="B")    # rec2 active
i = m.revert_intent("region")                                          # base = rec2 id
m.remember("update: region is A again", key="region", object="A")      # rec3 — ABA re-assertion (new id)
res = m.submit_revert(i, signer(i))
R["relative_conflicts_on_ABA_move"] = (res.get("reason") == "conflict")  # immune: does not land on look-alike
i2 = m.revert_intent("region"); res2 = m.submit_revert(i2, signer(i2))   # fresh revert at the moved state
R["relative_reverts_id_linked_predecessor"] = (res2.get("ok") and res2.get("reverted_to_object") == "B")

# ── ABSOLUTE, 0.7.15: the intent carries the target record id -> instance-bound. ───────────────
m2 = Mnemo(path=None, revert_pubkey=pk); m2.echo_guard = False
m2.remember("region is A [survey-1]", key="region", object="A")        # rec1 — the A the user means
m2.remember("correction: region is now B", key="region", object="B")
i3 = m2.restore_intent("region", "A")                                  # 0.7.15 mint: restore:region=A@<rec1>#..
R["absolute_intent_is_id_bound"] = bool(re.match(r"^restore:region=A@[0-9a-f]+#", i3))
# the gap: A re-asserted down another path, then legitimately re-killed to C
m2.remember("region is A [leak]", key="region", object="A")            # rec3 — look-alike, different id
m2.remember("correction: region is now C", key="region", object="C")   # current C
R["absolute_before_state"] = cur(m2)                                   # "C"
res3 = m2.submit_revert(i3, signer(i3))
R["absolute_landed_owes_the_land"] = res3.get("ok")                    # absolute is unconditional by design
R["absolute_reports_id_bound"] = res3.get("id_bound") is True

# a look-alike cannot masquerade as the minted target: forge a restore aimed at rec3's id, but the real
# adversarial point is that a CONTENT-only (legacy id-less) intent still resolves by value = the closed seam.
legacy = "restore:region=A#" + "deadbeefcafe0001"
res_legacy = m2.submit_revert(legacy, signer(legacy))
R["legacy_idless_still_content_resolves"] = (res_legacy.get("ok") and res_legacy.get("id_bound") is False)
# an id-bound intent naming a record that never held the key as that value is rejected (cannot fabricate)
forged = "restore:region=Z@" + "f" * 12 + "#" + "deadbeefcafe0002"
res_forged = m2.submit_revert(forged, signer(forged))
R["id_bound_rejects_nonexistent_target"] = (res_forged.get("reason") == "unknown_target")

print(json.dumps(R, indent=2))
ok = (R["relative_conflicts_on_ABA_move"] and R["relative_reverts_id_linked_predecessor"]
      and R["absolute_intent_is_id_bound"] and R["absolute_reports_id_bound"]
      and R["legacy_idless_still_content_resolves"] and R["id_bound_rejects_nonexistent_target"])
print("\nREADING:")
print("  RELATIVE: id-bound both ends -> conflicts on an ABA move, reverts the id-linked predecessor. IMMUNE.")
print("  ABSOLUTE (0.7.15): the intent now carries the target record id, so it binds to a specific INSTANCE,")
print("  not 'the current value that looks like X'. A same-value re-assertion is a different id. The legacy")
print("  id-less form still content-resolves (the seam we closed for new mints). Owes-the-land is preserved.")
print("\nALL PASS" if ok else "\nCHECK FAILED")
sys.exit(0 if ok else 1)
