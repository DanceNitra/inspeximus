"""The plugin's SessionStart names the MEMORY.md pointers Claude Code's loader cut, and stays silent otherwise.

The receipt logic itself is tested by mutation in the module's own tests below; this file checks the
two things a plugin user meets: the block reaches SessionStart's stdout when the index is over the cap,
and nothing about it appears when the index fits. The index path is pointed at a temp file through
CLAUDE_MEMORY_INDEX, the same override the standalone entry point honours.
"""
import json
import os
import subprocess
import sys

import pytest

import inspeximus.claude_code as cc
from inspeximus import memory_index_receipt as mir


def _index(n_lines: int, width: int = 20) -> str:
    return "".join("- [e%d](e%d.md) - %s\n" % (i, i, "x" * width) for i in range(n_lines))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_NO_INJECT", raising=False)
    return tmp_path


def _session_start_out(capsys):
    cc.session_start({"hook_event_name": "SessionStart", "cwd": os.getcwd()})
    out = capsys.readouterr().out
    return out


def test_an_index_over_the_line_cap_reaches_session_start_by_name(project, monkeypatch, capsys):
    idx = project / "MEMORY.md"
    idx.write_bytes(_index(205).encode())
    monkeypatch.setenv("CLAUDE_MEMORY_INDEX", str(idx))
    out = _session_start_out(capsys)
    assert "memory-index receipt" in out and "e204.md" in out and "e199.md" not in out


def test_an_index_that_fits_leaves_no_trace_in_session_start(project, monkeypatch, capsys):
    idx = project / "MEMORY.md"
    idx.write_bytes(_index(50).encode())
    monkeypatch.setenv("CLAUDE_MEMORY_INDEX", str(idx))
    assert "memory-index receipt" not in _session_start_out(capsys)


def test_a_missing_index_is_silent(project, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_MEMORY_INDEX", str(project / "absent.md"))
    assert "memory-index receipt" not in _session_start_out(capsys)


def test_session_start_output_is_still_one_json_envelope_with_the_receipt_inside(project, monkeypatch, capsys):
    """The host parses stdout as one object; a second block must join the envelope, not follow it."""
    idx = project / "MEMORY.md"
    idx.write_bytes(_index(205).encode())
    monkeypatch.setenv("CLAUDE_MEMORY_INDEX", str(idx))
    out = _session_start_out(capsys).strip()
    obj = json.loads(out)                                    # raises on two objects or bare text
    assert "e204.md" in json.dumps(obj)


# --- the cut rule itself ------------------------------------------------------------------


def test_the_unit_cap_counts_utf16_units_and_carriage_returns():
    """An LF index that fits with fewer than one unit per line to spare must be cut once CRLF adds one."""
    width = 121
    while mir.u16(_index(185, width)) > mir.UNIT_CAP - 1:
        width -= 1
    lf = _index(185, width)
    assert mir.UNIT_CAP - 185 < mir.u16(lf) <= mir.UNIT_CAP
    assert mir.receipt(lf) == ""
    crlf = lf.replace("\n", "\r\n")
    assert mir.u16(crlf) > mir.UNIT_CAP
    assert "NOT in this session's context" in mir.receipt(crlf), "a CR-blind count reports this index as fitting"


def test_an_astral_character_counts_as_two_units():
    """The loader compares JavaScript String.length; a Python len() would be short by one per emoji."""
    assert mir.u16("a") == 1 and mir.u16(chr(0x1F600)) == 2


def test_a_dropped_line_pointing_at_an_entry_the_window_has_is_not_reported():
    text = _index(205).replace("- [e204](e204.md)", "- [e204](e1.md)")
    out = mir.receipt(text)
    assert "e1.md" not in out and "4 pointer(s)" in out


def test_the_standalone_entry_point_reads_the_saved_before_state_to_four_names():
    """The same-day before/after published on claude-code#70555 came from a probe; the shipped module
    must read the same file to the same four names or the two instruments disagree on the cut rule."""
    saved = os.path.join(os.path.expanduser("~"), ".claude", "projects", "C--Users-Danculus-agora",
                         "memory", "MEMORY.md.pre-compaction-2026-09-13")
    if not os.path.isfile(saved):
        pytest.skip("the saved pre-compaction index is not on this machine")
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "inspeximus.memory_index_receipt", saved],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0
    assert "kept 180 of 184 lines (24,892 of 25,149 units); 4 pointer(s)" in r.stdout
    assert "public-repo-anon-git-identity.md" in r.stdout
