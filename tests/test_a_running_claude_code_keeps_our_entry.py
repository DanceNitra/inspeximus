"""A running Claude Code session must not put back an older ~/.claude.json over the entry we wrote.

Measured 2026-09-27 on Windows (tools/claude_json_overwrite_repro.py): Claude Code 2.1.280 and 2.1.283
kept the entry. A logged-in 2.1.280 session wrote the file again at exit, and our entry survived. This
runs the same harness against whatever Claude Code build is installed, so a future build that does write
a stale copy back fails here.

Opt-in, because it needs a Claude Code binary and a login, which CI does not have:
    INSPEXIMUS_CLAUDE_REPRO_BIN=<path to claude>  [INSPEXIMUS_CLAUDE_REPRO_AUTH=1 to run logged in]
Without a login the harness exits 3 ("Claude never wrote again, cannot tell"), and this test reports a
skip rather than a pass, because a session that never writes proves nothing.
"""
import os
import subprocess
import sys

import pytest

BIN = os.environ.get("INSPEXIMUS_CLAUDE_REPRO_BIN")
HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools",
                       "claude_json_overwrite_repro.py")


@pytest.mark.skipif(not BIN, reason="set INSPEXIMUS_CLAUDE_REPRO_BIN to a Claude Code binary to run this")
def test_a_running_claude_code_session_keeps_the_entry_we_wrote(tmp_path):
    args = [sys.executable, HARNESS, str(tmp_path / "work"), BIN, "installer"]
    if os.environ.get("INSPEXIMUS_CLAUDE_REPRO_AUTH") == "1":
        args.append("--auth")
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode == 3:
        pytest.skip("Claude Code never wrote the file after the installer (no login?); this run cannot tell")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
