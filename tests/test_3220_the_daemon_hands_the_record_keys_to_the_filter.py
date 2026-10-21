"""AUDIT-A 3.22 F-2, the daemon path: a served block comes with the key of each keyed line it renders.

The once-per-session filter withholds a keyed line only while it is the last text shown for its key, so a revert to an
earlier value is injected again. The filter runs in the hook process; the daemon renders the block, so the keys must
travel with the answer. Without them a daemon-served block fell back to text keys and the revert was withheld.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import hookd  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _ev, _req, _stop_started_daemons, daemon, project)


def test_the_answer_carries_the_key_of_each_rendered_decision(project, daemon):
    proj, sp = project
    r = daemon.answer(_req(daemon, proj))
    assert r["ok"], r
    text = json.loads(r["out"])["hookSpecificOutput"]["additionalContext"]
    lines = [ln for ln in text.split("\n") if ln.startswith("  * ")]
    assert lines, "CONTROL: the fixture's decisions are rendered"
    assert all(str(r["subjects"].get(ln, "")).startswith("decision::deploy") for ln in lines), (lines, r["subjects"])


def test_ask_hands_the_keys_to_the_hook(project, daemon):
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=3.0)
    assert hookd.LAST["outcome"] == "served", hookd.LAST
    subjects = hookd.LAST.get("subjects") or {}
    assert subjects and all(v.startswith("decision::deploy") for v in subjects.values()), subjects
