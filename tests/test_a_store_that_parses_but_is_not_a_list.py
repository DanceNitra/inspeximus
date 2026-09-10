"""A file that is valid JSON but is not a list of records must be refused, by name, untouched.

Parsing is not loading. Everything after the parse assumes a LIST of record dicts, and an object
was assigned straight through. The loop below it then iterated the object's KEYS:

    AttributeError: 'str' object has no attribute 'setdefault'

thrown from inside core.py, naming neither the file nor the shape. Ten bytes of garbage, meanwhile,
got a clean refusal that named the path. The worse error was on the nearer-miss input, and
`{"memories": [...]}` is the shape a stranger actually pointed at us.

It REFUSES rather than unwrapping, and that is deliberate: guessing which key holds the records
means guessing that a foreign file is a store at all, and being wrong there means saving over
someone's data. The message offers the unwrap as an instruction to the reader instead.

Every case asserts the file is BYTE-IDENTICAL afterwards. A refusal that still writes is the defect
this whole path exists to prevent: a truncated store once loaded as [] and the next save wrote that
empty list over it, five records in and one on disk.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(text):
    p = os.path.join(tempfile.mkdtemp(), "legacy.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


@pytest.mark.parametrize("payload,shape", [
    (json.dumps({"memories": [{"text": "x"}]}), "dict"),
    (json.dumps({"items": [{"text": "x"}]}), "dict"),
    (json.dumps({"a": 1, "b": 2}), "dict"),
    (json.dumps("a string"), "str"),
    (json.dumps(7), "int"),
])
def test_a_non_list_store_is_refused_and_never_written(payload, shape):
    p = _store(payload)
    before = open(p, "rb").read()
    with pytest.raises(ValueError) as e:
        Inspeximus(path=p)
    msg = str(e.value)
    assert p in msg, "the refusal must name the file, which the AttributeError never did"
    assert shape in msg, "and say what it found, so the reader knows it is the wrong file"
    assert open(p, "rb").read() == before, "a refusal that writes is worse than the crash"


def test_a_single_list_wrapper_is_told_how_to_fix_it():
    """The common near-miss earns an instruction, not just a refusal."""
    p = _store(json.dumps({"memories": [{"text": "x"}]}))
    with pytest.raises(ValueError) as e:
        Inspeximus(path=p)
    msg = str(e.value)
    assert "'memories'" in msg, "name the key that holds the list"
    assert "save that list as the whole file" in msg


def test_a_dict_with_two_lists_gets_no_guess():
    """Two candidate keys means we do not know which, and saying nothing is right."""
    p = _store(json.dumps({"memories": [], "archive": []}))
    with pytest.raises(ValueError) as e:
        Inspeximus(path=p)
    assert "wrapper" not in str(e.value)


def test_the_control_a_bare_list_still_opens_and_migrates():
    """Without this, the tests above would pass against a loader that refuses everything."""
    from inspeximus import sqlite_store as ss
    p = _store(json.dumps([]))
    m = Inspeximus(path=p)
    m.remember("a decision", tags=["decision"])
    assert ss.looks_like_sqlite(p), "a legacy JSON list must still open AND migrate to rows"
    assert len(Inspeximus(path=p).items) == 1
