#!/usr/bin/env python
"""Watch somebody else's published log and refuse to co-sign it if the history changed.

The code moved into the package (`inspeximus/witness_log.py`) so that a witness needs nothing but
`pip install inspeximus`. This wrapper stays because the RUNBOOK, the CI workflow and the first
three witness invitations name it, and a path in somebody else's cron is not ours to break.

    inspeximus witness watch --url <log> --state my_witness_state.json     # the same run
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus.witness_log import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
