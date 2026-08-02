"""Probe: regex_extractor must not mint keys from non-referring subjects.

Found 2026-07-20 while ingesting a real conversational corpus (MemOps, arXiv 2607.12893) — NOT by any
synthetic probe, because every existing probe fed the extractor clean declarative statements.

On natural prose the copula patterns fire on pronouns, expletives and interrogatives:
  "It is important to ..."      -> key 'it'
  "There is a growing ..."      -> key 'there'
  "These are just a few ..."    -> key 'these'
  "What is the significance ..."-> key 'what'
Those keys then collide across completely unrelated sentences, and keyed supersession dutifully RETIRES
the earlier record. Measured on one 3.7k-sentence transcript before the guard: 103 supersessions, 83% of
them driven by such a key — e.g. a universal-basic-income sentence retired because a London-landmark
sentence shared the subject 'what'. That is silent data loss in a feature the README advertises for
free text.

The guard: a subject that IS, or ENDS IN, a non-referring word yields no key (the extractor's documented
fallback -> plain append). Legitimate keys must be untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from inspeximus.core import Inspeximus, regex_extractor  # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok):
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(("PASS " if ok else "FAIL ") + name)


def main():
    # 1. non-referring subjects yield NO key
    for t in ["It is important to study the outcomes of these programs.",
              "There is a growing interest in UBI among policymakers.",
              "These are just a few examples of UBI policies.",
              "What is the historical significance of the site?",
              "This is a good idea.",
              "They are arriving tomorrow."]:
        check(f"no key for non-referring subject: {t[:44]!r}", regex_extractor(t) is None)

    # 2. subjects that merely END in one are rejected too (greedy lead-in capture)
    for t in ["Do you think there is a better approach?",
              "Let me know if there is an issue."]:
        check(f"no key when subject ends non-referring: {t[:40]!r}", regex_extractor(t) is None)

    # 3. legitimate keys are untouched
    # KEY SCHEME CHANGED in 1.90.0 for the first-person forms, deliberately, and this is the record of it:
    # `my X` now canonicalises to `self::X`, and a current-marking modifier folds away, so "my title",
    # "my current title" and "I work at ..." can land on one key across a correction chain instead of three
    # keys that never meet. Third-person and bare-copula keys below are byte-identical to before.
    # MIGRATION: a store written by an older regex_extractor holds the old keys, and a new write will not
    # supersede them. Re-key or accept that supersession applies from the upgrade forward.
    for t, want in [("My ZIP code is 94107", "self::zip code"),
                    ("My manager is Diane Kowalski", "self::manager"),
                    ("Correction: my current title is Data Analyst", "self::title"),
                    ("Alice's email is alice@example.com", "alice::email"),
                    ("The capital of France is Paris", "france::capital"),
                    ("The API rate limit is 500 rps", "api rate limit")]:
        got = regex_extractor(t)
        check(f"key preserved {want!r}", bool(got) and got[0] == want)

    # 4. END TO END — the failure this probe exists for: two unrelated "It is ..." sentences must NOT
    #    supersede each other, while a real correction on a real subject still must.
    m = Inspeximus(path=None)
    m.extractor = regex_extractor
    m.echo_guard = True
    m.remember("It is important to study the outcomes of these programs.")
    m.remember("It is used by the council to administer public services.")
    retired = [r for r in m.items if r.get("status") == "superseded"]
    check("unrelated 'It is ...' sentences do not retire each other", len(retired) == 0)

    m2 = Inspeximus(path=None)
    m2.extractor = regex_extractor
    m2.echo_guard = True
    m2.remember("My current title is Junior Data Analyst")
    m2.remember("My current title is Data Analyst")
    top = (m2.recall("what is my current title", k=1) or [{}])[0].get("text", "")
    retired2 = [r for r in m2.items if r.get("status") == "superseded"]
    check("a real correction still supersedes", len(retired2) == 1 and "Junior" not in top)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
