"""One place a probe writes its receipt, because the rule kept living in the callers.

WHY THIS EXISTS. A probe's committed `.result.json` is the measurement someone cites. The test
suite executes probes as smoke tests, in parallel, on a machine already running several thousand
other tests, so the numbers a suite run produces describe a different machine. A probe that writes
its receipt from inside the suite therefore replaces a measurement with noise, silently, and the
first sign is a dirty working tree that looks like an ordinary edit.

The guard for this is one line, `if os.environ.get("PYTEST_CURRENT_TEST"): return`, and it was
added by hand three separate times: 07b281c to two probes, then again on 2026-09-10 to the lock
probe, then again the same day to identity_gate_supersession after CI reported
`running an example dirtied tracked files`. Measured at that point: 28 tracked probes wrote a
tracked receipt with no such guard.

Our own rule for this shape says a fix applied at N call sites belongs one level down. So the guard
lives here, under the write, instead of beside each caller.

WHAT IT DOES NOT DO. It does not decide what the receipt contains, and it does not make a probe
correct. It answers one question -- may this run persist its numbers -- and answers it the same way
everywhere.

    from _receipt import write_receipt
    write_receipt(__file__, out)                       # <probe>.result.json beside the probe
    write_receipt(__file__, out, name="custom.json")   # when the receipt is not named after it
"""
from __future__ import annotations

import json
import os


def suppressed() -> bool:
    """True when this run must not persist a receipt.

    PYTEST_CURRENT_TEST is set by pytest for the duration of each test, in every worker, so it is
    true exactly when a probe is being executed BY the suite rather than by a person or by CI's own
    measurement step. INSPEXIMUS_NO_RECEIPT is the manual override for the same intent.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("INSPEXIMUS_NO_RECEIPT"))


def receipt_path(probe_file: str, name: str | None = None) -> str:
    """The receipt path for a probe, derived from the probe's own __file__."""
    if name:
        return os.path.join(os.path.dirname(os.path.abspath(probe_file)), name)
    return os.path.splitext(os.path.abspath(probe_file))[0] + ".result.json"


def write_receipt(probe_file: str, obj, name: str | None = None, indent: int = 1) -> str | None:
    """Write `obj` as the probe's receipt, unless this run must not persist one.

    Returns the path written, or None when suppressed. The caller gets told either way, so a probe
    can print what happened rather than leaving the reader to guess whether a file changed.
    """
    path = receipt_path(probe_file, name)
    if suppressed():
        print("  running under the suite, so the receipt is NOT rewritten: these numbers describe a "
              "saturated machine, not the committed measurement.")
        return None
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False)
        fh.write("\n")
    return path
