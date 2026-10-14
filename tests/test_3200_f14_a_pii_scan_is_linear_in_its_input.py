"""3.20, F14 (AUDIT-A, after the Claude Security scan): detect_pii and redact_pii are linear in their input.

THE DEFECT, two of them. (1) The e-mail pattern used unbounded `+` quantifiers, so "a-a-a-..." backtracked from
every word boundary: redact_pii("a-" * 64000) took 5.8 s and matched nothing. (2) Every candidate was checked
against every span claimed so far, and redact_pii rebuilt the whole string once per hit: 20,000 addresses took 13 s.
Both are reachable with text an agent writes: recall(redact_pii=True), forget_pii, pii_report and pii_detect=True.

WHAT IS PINNED HERE. The e-mail pattern is bounded by RFC 5321 (local part 64, domain 255, top-level label 63).
The claimed spans are kept sorted and checked with bisect, and redact_pii builds its output in one pass. The
results are unchanged except for a local part over 64 characters, which RFC 5321 does not allow: the address is
still found and masked, from its last 64 characters. Measured on 3,000 varied texts (2,606 with hits) against
v3.19.1: one difference, that case.

The time limits below are 20 times what the fix measures on PC1 and under half of what v3.19.1 took.
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import core  # noqa: E402


def _timed(fn, *a):
    t0 = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t0


def test_a_text_that_backtracks_the_email_pattern_is_scanned_in_linear_time():
    out, dt = _timed(core.redact_pii, "a-" * 64000)
    assert out == ("a-" * 64000, {}), "CONTROL: nothing in it is PII"
    assert dt < 2.0, "redact_pii took %.2f s on 128,000 characters" % dt


def test_many_hits_are_claimed_and_masked_in_linear_time():
    text = "x@ex.io " * 20000
    (masked, counts), dt = _timed(core.redact_pii, text)
    assert counts == {"email": 20000} and masked == "[EMAIL] " * 20000, "CONTROL: every address is masked"
    assert dt < 4.0, "redact_pii took %.2f s on 20,000 hits" % dt
    found, dt = _timed(core.detect_pii, text)
    assert len(found["email"]) == 20000 and dt < 4.0, dt


def test_the_email_pattern_has_no_unbounded_quantifier():
    src = dict(core._PII_PATTERNS)["email"].pattern
    assert not re.search(r"\]\+|\{\d+,\}", src), "an unbounded quantifier is back in the e-mail pattern: %s" % src


def test_a_more_specific_span_still_wins_and_a_rejected_one_stays_free():
    found = core.detect_pii("ssn 123-45-6789 and card 4111 1111 1111 1111 and call 1234 5678 9012 3 today")
    assert found["ssn"] == ["123-45-6789"]
    assert [c.strip() for c in found["credit_card"]] == ["4111 1111 1111 1111"]   # the pattern keeps a trailing separator
    # 1234 5678 9012 3 is 13 digits and fails Luhn, so credit_card rejects it and leaves its text for `phone`
    assert found.get("phone") == ["1234 5678 9012 3"], found


def test_redaction_keeps_the_text_between_hits_and_counts_by_type():
    masked, counts = core.redact_pii("mail a@b.io or c@d.io, ssn 123-45-6789.")
    assert masked == "mail [EMAIL] or [EMAIL], ssn [SSN]."
    assert counts == {"email": 2, "ssn": 1}
    masked, counts = core.redact_pii("mail a@b.io, ssn 123-45-6789", types=["ssn"])
    assert masked == "mail a@b.io, ssn [SSN]" and counts == {"ssn": 1}


def test_an_address_with_an_over_long_local_part_is_still_found_and_masked():
    local = "a." * 40 + "smith"                        # 85 characters, over RFC 5321's 64
    masked, counts = core.redact_pii("write to %s@example.com today" % local)
    assert counts == {"email": 1} and "@example.com" not in masked
