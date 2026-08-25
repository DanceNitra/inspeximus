"""No test may decode a child's output with the machine's locale codec.

Measured 2026-08-25, on the release check for 2.20.1. `test_a_stored_record_cannot_forge_the_hooks
_own_header` failed with `AttributeError: 'NoneType' object has no attribute 'count'`. The cause was
two files away from the assertion: `subprocess.run(..., text=True)` with no `encoding=` decodes with
`locale.getpreferredencoding(False)`, which on this machine is **cp1250**. The child ran under
`-X utf8` and emitted UTF-8, so byte 0x98 killed the reader thread, `r.stdout` came back None, and
the test reported a product failure it had not measured.

79 call sites across 42 test files had the identical construction. ONE was failing, because only its
output happened to contain a byte cp1250 cannot decode; the other 78 were one non-ASCII character
away from the same crash, and would have failed on this machine and passed in UTF-8 CI. A green
suite that depends on which characters a child happens to print is not measuring what it claims.

This is CLAUDE.md rule 11 (the console is not UTF-8) meeting the fix-the-class rule: the reported
instance is not the defect, the construction is. So the construction is banned here rather than
corrected 79 times and left free to come back on the 80th.
"""
from __future__ import annotations

import io
import pathlib
import re

TESTS = pathlib.Path(__file__).resolve().parent
CALL = re.compile(r"subprocess\.run\((?:[^()]|\([^()]*\))*?\)", re.S)


def _offenders() -> list[str]:
    out = []
    for p in sorted(TESTS.glob("*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue
        s = io.open(p, encoding="utf-8").read()
        for m in CALL.finditer(s):
            blk = m.group(0)
            if "text=True" in blk and "encoding=" not in blk:
                out.append(f"{p.name}:{s[:m.start()].count(chr(10)) + 1}")
    return out


def test_no_test_decodes_a_child_with_the_locale_codec():
    bad = _offenders()
    assert not bad, (
        "subprocess.run(text=True) with no encoding= decodes with the machine's locale codec "
        "(cp1250 here), so a child printing UTF-8 returns stdout=None and the assertion below it "
        "reports a defect it never measured. Add encoding=\"utf-8\", errors=\"replace\":\n  "
        + "\n  ".join(bad))


def test_the_detector_can_actually_fail():
    """The guard above is an absence check, which is the shape that passes when it sees nothing.

    So hand it text it MUST flag. Without this, deleting the body of `_offenders` leaves a green
    test that has measured nothing -- the failure mode this repository meets most often.
    """
    sample = 'r = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True)'
    m = CALL.search(sample)
    assert m, "the call-site pattern no longer matches an ordinary subprocess.run"
    blk = m.group(0)
    assert "text=True" in blk and "encoding=" not in blk, "the offending shape is no longer detected"
    ok = 'r = subprocess.run(["x"], capture_output=True, text=True, encoding="utf-8")'
    mo = CALL.search(ok)
    assert mo and "encoding=" in mo.group(0), "a corrected call must NOT be flagged"
