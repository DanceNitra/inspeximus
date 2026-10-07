"""F-36 a: every numeric field, every number the arithmetic cannot use (AUDIT-A). About a minute."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bad_record_io import _hand_edit, _project, _prompt, cc, sandbox  # noqa: E402,F401
from inspeximus import _isolate  # noqa: E402

# ── F-36 a: numbers ───────────────────────────────────────────────────────────────────────────────────────────────────

NUMERIC = [f for f, _ in __import__("inspeximus.core", fromlist=["_NUMERIC_FIELDS"])._NUMERIC_FIELDS]
NUMBERS = [("inf", float("inf")), ("-inf", float("-inf")), ("nan", float("nan")), ("bigint", 10 ** 400), ("-bigint", -10 ** 400)]


def test_every_numeric_field_is_listed():
    assert {"value", "good", "bad", "ts", "last_access", "valid_from"} <= set(NUMERIC), NUMERIC


@pytest.mark.parametrize("field", NUMERIC)
def test_a_number_the_arithmetic_cannot_use_is_repaired_at_load_in_every_numeric_field(sandbox, field):
    for label, value in NUMBERS:
        p, store = _project(sandbox, "n_%s_%s" % (field, label.replace("-", "m")))
        _hand_edit(store, lambda d, f=field, v=value: d.__setitem__(f, v))
        m = cc._store(p)
        victim = [r for r in m.items if r.get("key") == "victim"][0]
        v = victim.get(field)
        assert v is None or (v - v == 0.0), (field, label, v)                  # finite, or dropped for the fixed fallback
        assert field in victim["meta"]["malformed"], (field, label)
        assert victim["meta"]["quarantined"]["reason"] == "malformed_record"
        _isolate._SAID.clear()
        out, err = _prompt(p)
        assert "release process uses the gate" in out and "failed" not in err, (field, label, err[-120:])
        assert "left out of this answer" not in err, "the load repair must make the isolation unnecessary: %s %s" % (field, label)

