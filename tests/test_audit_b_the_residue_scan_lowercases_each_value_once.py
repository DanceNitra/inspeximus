"""AUDIT-B B-18: the in-store residue scan must lowercase each erased value once, not once per record.

`erasure_residue.scan_records` runs inside every `forget` and `forget_subject`. Its inner loop compared
`v.lower() in low`, so every erased value was lowercased again for every surviving record and field.
The erased values are whole record texts, so that is O(values x records) string copies. Measured
2026-09-27 on a copy of the MCP store (10,934 records, a subject with 1,011 records): 2,008,775
`str.lower` calls and 9.58 s of the 12.8 s erasure.

The counter is the number of `lower` calls while the scan runs, read with `sys.setprofile`, which
reports every call into a C method. The control requires the scan to find the residue it exists to
find, so the counter cannot drop by never comparing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.erasure_residue import scan_records  # noqa: E402

N, K = 60, 12


def _count_lower(fn):
    box = [0]

    def prof(frame, event, arg):
        if event == "c_call" and getattr(arg, "__name__", "") == "lower":
            box[0] += 1

    sys.setprofile(prof)
    try:
        out = fn()
    finally:
        sys.setprofile(None)
    return out, box[0]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-18: every erased value is lowercased again for every record and field")
def test_each_erased_value_is_lowercased_once():
    values = [f"Alice Example lives at {i} Elm Street, Springfield" for i in range(K)]
    records = [{"id": f"r{i}", "text": f"unrelated note {i}", "object": f"obj {i}"} for i in range(N)]
    records.append({"id": "leak", "text": "summary: " + values[3], "object": None})
    res, lowers = _count_lower(lambda: scan_records(records, values))

    # CONTROL: the residue is found in the one record that holds it.
    if [f["id"] for f in res["findings"]] != ["leak"] or res["checked_records"] != N + 1:
        pytest.fail(f"control: findings {res['findings']}, checked {res['checked_records']}")
    budget = K + 2 * (N + 1)        # each value once, each record field once
    assert lowers <= budget, f"{lowers} lower() calls for {K} values over {N + 1} records (expected <= {budget})"
