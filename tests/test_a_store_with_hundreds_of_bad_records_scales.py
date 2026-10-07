"""F-36: 60, 200 and 1,000 bad records of 2,000 (AUDIT-A). Slow (about a minute), so the mutation entries use the quick file."""
import json
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bad_record_io import QUERY, _prompt, cc, sandbox  # noqa: E402,F401
from inspeximus import _isolate  # noqa: E402
from test_audit_a_3165_second_review import _big_store, _served_keys  # noqa: E402


@pytest.mark.parametrize("n,bad_every", [(2000, 33), (2000, 10), (300, 5)])     # about 60, 200 and 60 bad records
def test_f36_sixty_and_two_hundred_bad_records_of_two_thousand_leave_the_hook_answering(sandbox, n, bad_every):
    p, store, bad = _big_store(sandbox, "big_%d_%d" % (n, bad_every), n, bad_every)
    assert len(bad) >= 55, len(bad)
    _isolate._SAID.clear()
    t0 = time.monotonic()
    out, err = _prompt(p)
    took = time.monotonic() - t0
    assert "release process uses the gate" in out and "failed" not in err, err[-200:]
    assert "left out of this answer" in err
    assert took < 6.0, "the isolation took %.1f s" % took
    _isolate._SAID.clear()
    served = _served_keys(p, 2500)
    assert not (served & bad), "a bad record was served"
    good = {str(i) for i in range(n)} - bad
    assert len(good - served) <= (_isolate.CHUNK - 1) * 0 or not (good - served), \
        "a good record was hidden although the search finished: %d" % len(good - served)


def test_f36_one_thousand_bad_records_still_leave_the_hook_answering(sandbox):
    p, store, bad = _big_store(sandbox, "thousand", 2000, 2)
    assert len(bad) == 1000
    _isolate._SAID.clear()
    out, err = _prompt(p)
    assert "release process uses the gate" in out and "failed" not in err, err[-200:]
    assert "left out of this answer" in err
    _isolate._SAID.clear()
    assert not (_served_keys(p, 2500) & bad)

