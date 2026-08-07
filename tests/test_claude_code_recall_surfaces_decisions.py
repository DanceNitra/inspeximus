"""The recall hook printed nothing whenever it had the most to say. Two independent causes.

WHAT HAPPENED. The hook injects "what did we decide, and why" before each prompt. For a full working
day it surfaced `ran: git status` and never a decision, while the decision that answered the prompt sat
in the store the whole time. Both causes are silent by construction, which is why it took a day:

  1. WRONG FILE. The hook reads the PROJECT coding store; `remember_decision` over MCP writes to the
     store the MCP server was configured with. Measured on the deployment where this was found: the
     project store held 6,779 records, of which 5,857 were `ran: ...` bash captures and 16 were
     decisions; the MCP store held 350 records, ALL of them decisions. The writes were happening,
     correctly typed, into a file the reader never opened.

  2. THE CONSOLE CODEPAGE DELETED THE BLOCK. Decision prose contains em dashes, arrows, Cyrillic and
     Chinese. On a cp1250 console `print()` raised UnicodeEncodeError, the caller swallowed it, and the
     process exited 0 with EMPTY stdout and EMPTY stderr. Measured: the same event emits 18,032 bytes
     under PYTHONIOENCODING=utf-8 and 0 bytes under cp1250. Not truncated, not mojibake -- nothing,
     indistinguishable from "no relevant memory". The richer the memory, the likelier a character that
     erases the whole block, so the hook went quiet exactly when it mattered.

The bound is here for the same reason: eight unbounded records is 18 KB of context spent before the
user has typed. The block is a POINTER to a record, not the record.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from inspeximus import Inspeximus  # noqa: E402


def _decision_store(tmp, text):
    p = os.path.join(tmp, "decisions.json")
    m = Inspeximus(path=p)
    m.remember(text, tags=["decision"], key="decision::probe", object="probe")
    m.flush()
    return p


def _project(tmp):
    """A project dir whose own store holds only mechanics -- the state that produced the bug."""
    d = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(d, ".inspeximus"), exist_ok=True)
    m = Inspeximus(path=os.path.join(d, ".inspeximus", "coding_memory.json"))
    for i in range(5):
        m.remember(f"ran: git status --short  # {i}", tags=["bash"], mtype="episodic")
    m.flush()
    return d


def _run(cwd, prompt, env_extra, encoding="cp1250"):
    """Drive the hook exactly as the host does: a JSON event on stdin, output read as BYTES.

    Bytes, not text: a harness that decodes the pipe with the console codec dies on the same character
    the hook died on, and then reports the crash it caused as an empty result.
    """
    env = dict(os.environ)
    env.pop("INSPEXIMUS_DECISION_STORE", None)
    env["PYTHONIOENCODING"] = encoding
    env.update(env_extra)
    ev = json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": cwd, "prompt": prompt}).encode()
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"],
                       input=ev, capture_output=True, env=env, cwd=ROOT)
    return r.returncode, (r.stdout or b"")


NON_ASCII = ("DECISION: we keep the keyed supersession — because: the товарищ "
             "form-of-address flip and the 跨框架 matrix both need it → deterministic")


def test_a_decision_with_an_em_dash_is_not_deleted_by_the_console_codepage():
    """THE one that cost a day. cp1250 cannot encode an em dash; the block must survive anyway."""
    with tempfile.TemporaryDirectory() as tmp:
        ds = _decision_store(tmp, NON_ASCII)
        cwd = _project(tmp)
        rc, out = _run(cwd, "what did we decide about supersession", {"INSPEXIMUS_DECISION_STORE": ds})
        assert rc == 0
        assert out, ("the hook emitted NOTHING on a cp1250 console. Exit code 0 and an empty stderr, "
                     "so the caller cannot tell this from 'no relevant memory found'.")
        assert b"keyed supersession" in out, out[:200]


def test_the_same_decision_survives_a_utf8_console_too():
    """Control: if this ever fails, the fix broke the ordinary path rather than the cp1250 one."""
    with tempfile.TemporaryDirectory() as tmp:
        ds = _decision_store(tmp, NON_ASCII)
        cwd = _project(tmp)
        rc, out = _run(cwd, "what did we decide about supersession",
                       {"INSPEXIMUS_DECISION_STORE": ds}, encoding="utf-8")
        assert rc == 0 and b"keyed supersession" in out


def test_the_external_decision_store_is_read_at_all():
    """The project store holds only mechanics; without the second store the decision is unreachable."""
    with tempfile.TemporaryDirectory() as tmp:
        ds = _decision_store(tmp, "DECISION: the B-series scenarios were already computed and posted")
        cwd = _project(tmp)
        _, without = _run(cwd, "were the B-series scenarios computed", {})
        _, with_ = _run(cwd, "were the B-series scenarios computed", {"INSPEXIMUS_DECISION_STORE": ds})
        assert b"B-series" not in without, (
            "fixture no longer reproduces: the project store already knows this, so the test cannot "
            "show that the external store is what supplied it")
        assert b"B-series" in with_, "the configured decision store was not consulted"


def test_the_block_is_bounded():
    """Eight unbounded records was 18 KB of context before the user typed anything."""
    with tempfile.TemporaryDirectory() as tmp:
        ds = _decision_store(tmp, "DECISION: " + ("x" * 12000))
        cwd = _project(tmp)
        rc, out = _run(cwd, "what did we decide", {"INSPEXIMUS_DECISION_STORE": ds})
        assert rc == 0 and out
        assert len(out) < 6000, f"recall block is {len(out)} bytes; the per-record cap is not applied"


def test_a_missing_or_broken_decision_store_does_not_silence_the_hook():
    """Fail-open: a second store is an enrichment, and a hook that raises costs the user their turn."""
    with tempfile.TemporaryDirectory() as tmp:
        cwd = _project(tmp)
        for bad in (os.path.join(tmp, "does_not_exist.json"), __file__):
            rc, out = _run(cwd, "git status", {"INSPEXIMUS_DECISION_STORE": bad})
            assert rc == 0, f"hook exited {rc} on a bad decision store {bad!r}"


def test_unset_leaves_todays_behaviour_exactly():
    """No env, no second store, no change -- the project store's own hits still print."""
    with tempfile.TemporaryDirectory() as tmp:
        cwd = _project(tmp)
        rc, out = _run(cwd, "git status", {})
        assert rc == 0
        assert b"[inspeximus]" in out and b"ran: git status" in out
