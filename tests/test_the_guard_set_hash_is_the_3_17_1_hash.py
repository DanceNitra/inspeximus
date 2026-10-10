"""The guard-set hash is the one 3.17.1 shipped, on every Python (3.18, AUDIT-A F-5, AUDIT-B F-1).

`_guard_set_hash()` hashes the source of `_instruction_shape`, `_stuffing` and `Inspeximus._assess_read_guards`, docstring
and comments included, with the guards' inputs and the Unicode database version. A stamp on a record is valid only under
the hash it was made with. So ANY change to that text, a comment included, makes every stamp in every user's store read
as foreign: the prompt hook assesses those records again on every prompt (about 0.36 s on our project store) until its
background re-stamp finishes, and the decision store is never re-stamped by the hook.

3.18 had changed only the docstring of `_assess_read_guards`, and that moved the hash. This test pins the 3.17.1 value
for each Unicode version a supported Python ships. If it fails, the change re-stamps every user's store: revert the
text, or bump deliberately, name it in the CHANGELOG, and update the table here.
"""
from __future__ import annotations

import os
import sys
import unicodedata

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import core  # noqa: E402

#: `_guard_set_hash()` of v3.17.1, by `unicodedata.unidata_version`, measured on PC1 with the v3.17.1 tag checked out.
V3171 = {
    "13.0.0": "cf5e808877e879e126b732d717aafb4c",    # Python 3.9, 3.10
    "14.0.0": "19901c03d1c2df4c1c809b569f124f96",    # Python 3.11
    "15.0.0": "12d9db48b93529c38a4c47cdd1cbe3cd",    # Python 3.12
    "15.1.0": "066764e26ab66cd04e46b6b3d6a69fa9",    # Python 3.13
    "16.0.0": "14e25caadae0514222679ac078141c42",    # Python 3.14
}


def test_the_guard_set_hash_is_the_3_17_1_hash():
    want = V3171.get(unicodedata.unidata_version)
    if want is None:
        pytest.skip("no 3.17.1 hash recorded for Unicode %s (Python %s): add it from a v3.17.1 checkout"
                    % (unicodedata.unidata_version, sys.version.split()[0]))
    core._GUARD_SET = None                                      # computed once per process: recompute it here
    try:
        got = core._guard_set_hash()
    finally:
        core._GUARD_SET = None
    assert got == want, (
        "the guard-set hash is %s, 3.17.1 shipped %s on Unicode %s. This change re-stamps every user's store: every "
        "stamp made by 3.17.1 reads as foreign, and the prompt hook re-assesses those records on every prompt until "
        "its re-stamp ends. The hash covers the source of _instruction_shape, _stuffing and _assess_read_guards, "
        "docstrings and comments included." % (got, want, unicodedata.unidata_version))
