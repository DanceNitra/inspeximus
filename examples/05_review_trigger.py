"""Read-path review trigger (mnemo 1.9.2-1.9.7) — the mirror of a write-time hold-for-review.

A store can be confidently WRONG: a correction lands, the record settles, and later a contradicting observation
arrives. You do not want to silently trust every contradiction (an attacker or a stray transcript line can
mint them), nor silently ignore it (a real correction never gets seen). observe() reopens a settled record for
STEWARD REVIEW — but only once the contradiction is CORROBORATED, so a lone restatement stays an echo. It never
supersedes; a human/steward closes the review with resolve_reopened().

Run:  python examples/05_review_trigger.py
"""
from mnemo import Mnemo


def main():
    m = Mnemo(path=None); m.echo_guard = True

    m.remember("the deploy region is Frankfurt", key="svc/region", object="Frankfurt")
    m.remember("correction: the deploy region is now Ohio", key="svc/region", object="Ohio")
    print("current value after correction:", m.recall("svc/region", k=1)[0]["text"])

    # A lone contradiction with one ground does NOT reopen — a stray line shouldn't flip a settled record.
    r1 = m.observe("someone claims it's Berlin", key="svc/region", object="Berlin", support=["slack-msg-8842"])
    print(f"\n1 contradiction (ground: slack-msg-8842)  -> reopened={r1['reopened']}, pending={r1['pending']}/"
          f"{r1['need']}  (held, not acted on)")

    # Replaying the SAME ground is an echo — corroboration counts DISTINCT novel grounds, not repeated emissions.
    r2 = m.observe("Berlin again", key="svc/region", object="Berlin", support=["slack-msg-8842"])
    print(f"replay the same ground                     -> reopened={r2['reopened']}, echo={r2.get('echo')}")

    # A SECOND, independent ground corroborates -> the record reopens for review (it is NOT auto-changed).
    r3 = m.observe("Berlin, per the infra audit", key="svc/region", object="Berlin", support=["audit-2026-Q3"])
    print(f"2nd independent ground (audit-2026-Q3)     -> reopened={r3['reopened']}, "
          f"surfaced_prior={r3['surfaced_prior']!r}")

    print("\nreview queue (mirror of a write-time hold-for-review):")
    for item in m.reopened():
        print(f"  key={item['key']}  reason={item['reason']}  contradiction={item.get('contradiction')!r}  "
              f"still-current-value stays in recall until a steward decides")

    # recall STILL returns the current value while the record is under review (an agent left with nothing is
    # worse) — but the hit now CARRIES the review signal, so the agent can branch instead of trusting blindly.
    hit = m.recall("svc/region", k=3)[0]
    print("recall during review still returns current:", "Ohio" in hit["text"])
    print(f"...and the hit is marked: under_review={hit.get('under_review')}, "
          f"reason={hit.get('review_reason')!r}, prior={hit.get('review_prior')!r}")
    if hit.get("under_review"):
        print("an agent seeing this should hedge or defer, e.g.: "
              f"'my records say {hit['text'].split()[-1]}, but that value is under review'")

    # Steward closes it. keep_current = false alarm; reaffirm_prior = restore the surfaced prior via the
    # authorized revert path. Here we judge it a false alarm and keep Ohio.
    rid = m.reopened()[0]["id"]
    out = m.resolve_reopened(rid, "keep_current")
    print(f"\nsteward resolves {out['decision']}          -> review queue now empty: {m.reopened() == []}, "
          f"recall hit clean again: {'under_review' not in m.recall('svc/region', k=1)[0]}")

    print("\nHonest scope: observe() FLAGS, it never decides. Distinguishing a legitimate contradiction from an\n"
          "injected one is an authority call, not a content call. Pass Mnemo(support_authorities=[...]) to\n"
          "require signed grounds (self-minted grounds then count zero), and a steward still owns the verdict.")


if __name__ == "__main__":
    main()
