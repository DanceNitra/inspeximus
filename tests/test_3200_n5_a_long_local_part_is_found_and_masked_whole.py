"""3.20, AUDIT-A N-5 (Info): an e-mail address with a long local part is found, and masked whole.

THE DEFECT. F14 bounded the e-mail pattern's local part at RFC 5321's 64 characters. A local part of 65 or more
characters with no word boundary inside was then not found at all, and a dotted one was masked only from its last 64
characters: `john.smith.` x 7 + `doe@example.com` became `john.smith.john.[EMAIL]` (AUDIT-A dr/ss5/f14_diff.py, on
dcce3660).

WHAT IS PINNED HERE. The local part's bound is 254, the longest path RFC 5321 allows for a whole address. Both shapes
are found and masked with nothing of the local part left visible, up to 254 characters.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import core  # noqa: E402


@pytest.mark.parametrize("local", ["a" * 65, "a" * 254, "john.smith." * 7 + "doe", "x" * 120 + ".y"],
                         ids=["65 unbroken", "254 unbroken", "81 dotted", "122 dotted"])
def test_a_long_local_part_is_found_and_masked_whole(local):
    text = "write to %s@example.com today" % local
    assert core.detect_pii(text).get("email") == [local + "@example.com"]
    masked, counts = core.redact_pii(text)
    assert masked == "write to [EMAIL] today" and counts == {"email": 1}, masked


def test_control_an_ordinary_address_is_unchanged():
    masked, counts = core.redact_pii("mail alice.smith@example.com now")
    assert masked == "mail [EMAIL] now" and counts == {"email": 1}
