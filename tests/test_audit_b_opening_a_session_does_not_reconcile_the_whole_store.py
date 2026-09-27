"""AUDIT-B B-20: opening a session must not force a full reconcile of the store.

`close_session` sets `_full_reconcile = True` so that "the one complete diff a session pays for happens
here, once". It sets it even for `write=False`, which its docstring calls a preview that stores nothing.
`open_session` calls exactly that preview to check whether an earlier session was left open, so the
marker it writes next paid a full reconcile too: every row serialised and compared, twice per session
boundary. SessionStart runs `open_session` on the project store. Measured 2026-09-27 on a 2,000-record
store: 2,001 row serialisations for `open_session`, and 4,005 for the whole boundary.

The counter is the number of full-diff saves (`sqlite_store.save` without `dirty`) while opening a
session on a freshly opened store. The control closes the session and requires the one full reconcile
the docstring promises, so the counter cannot reach zero by never reconciling.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus import sqlite_store as ss  # noqa: E402


def test_opening_a_session_saves_only_what_it_wrote(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    p = str(tmp_path / "s.json")
    m = Inspeximus(p)
    for i in range(200):
        m.remember(f"note {i} about the release", key=f"k{i}", mtype="episodic")
    m.flush()

    full = [0]
    real = ss.save

    def counting(path, items, before, dirty=None, rewrite_all=False, **k):
        if dirty is None or rewrite_all:
            full[0] += 1
        return real(path, items, before, dirty=dirty, rewrite_all=rewrite_all, **k)

    monkeypatch.setattr(ss, "save", counting)

    def measure(steps):
        full[0] = 0
        h = Inspeximus(p)
        steps(h)
        h.flush()
        return full[0]

    # CONTROL: a real close still pays the one full reconcile it exists for.
    at_close = measure(lambda h: h.close_session("s0"))
    if at_close != 1:
        pytest.fail(f"control: closing a session made {at_close} full reconciles, expected 1")

    at_open = measure(lambda h: h.open_session("s1"))
    after_preview = measure(lambda h: (h.close_session("s1", write=False),
                                       h.remember("the release moved to Friday", tags=["decision"])))
    assert at_open == 0, f"opening a session on a fresh store made {at_open} full reconcile(s)"
    assert after_preview == 0, f"a write=False preview made the next write a full reconcile ({after_preview})"
