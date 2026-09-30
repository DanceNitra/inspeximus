"""A-27: the memory-index receipt in SessionStart names at most a fixed number of pointers.

The hook bounds its digest block (INSPEXIMUS_SESSION_MAX_CHARS, 1,200 characters, "the hard size bound on
the injected block") and its files block (600), but the receipt listed every pointer the loader dropped,
about 40 characters each, in every session: 2,344 characters at 250 index lines, 12,345 at 500, 32,347 at
1,000. At 1,000 lines the receipt alone was larger than the 25,000-unit cap the loader applies to the whole
index. The receipt now names the last INSPEXIMUS_RECEIPT_MAX_POINTERS (default 20) dropped pointers, says
how many earlier ones it left out, and names the command that prints all of them.
"""
import json
import os
import subprocess
import sys

import pytest

import inspeximus.claude_code as cc
import inspeximus.memory_index_receipt as mir
from inspeximus.memory_index_receipt import receipt

DEFAULT_MAX_POINTERS = getattr(mir, "DEFAULT_MAX_POINTERS", 20)     # absent before A-27: fail on the bound, not on the import


def _index(n: int) -> str:
    return "# Memory Index\n" + "".join(
        f"- [Note {i} about a decision](note-{i}-about-a-decision.md) - a one-line hook for note {i}\n"
        for i in range(n))


def test_the_receipt_is_bounded_however_many_pointers_were_dropped():
    r = receipt(_index(1000), max_pointers=DEFAULT_MAX_POINTERS)
    assert len(r) <= 2000, f"{len(r)} characters for one receipt"


def test_the_bounded_receipt_still_tells_the_truth_about_what_it_left_out():
    r = receipt(_index(1000), max_pointers=20)
    assert "801 pointer(s)" in r, "the head must still count every dropped pointer"
    assert "note-999-about-a-decision.md" in r and "note-980-about-a-decision.md" in r
    assert "note-979-about-a-decision.md" not in r, "the earlier pointers are counted, not named"
    assert "... and 781 earlier pointer(s)" in r and "prints all 801" in r
    assert "python -m inspeximus.memory_index_receipt" in r
    assert r.count("\n  - ") == 20


def test_control_no_cap_names_every_pointer_as_the_command_line_does():
    r = receipt(_index(1000))
    assert r.count("\n  - ") == 801 and "earlier pointer(s)" not in r


def test_a_small_cut_is_listed_in_full():
    from inspeximus.memory_index_receipt import omitted
    dropped = len(omitted(_index(205))[4])
    assert 0 < dropped <= 20, "control: the fixture must drop a few pointers, not none and not many"
    r = receipt(_index(205), max_pointers=20)
    assert "earlier pointer(s)" not in r and r.count("\n  - ") == dropped


def _session_start_receipt(tmp_path, monkeypatch, capsys, lines, cap=None):
    idx = tmp_path / "MEMORY.md"
    idx.write_bytes(_index(lines).encode())
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_NO_INJECT", raising=False)
    monkeypatch.setenv("CLAUDE_MEMORY_INDEX", str(idx))
    if cap is None:
        monkeypatch.delenv("INSPEXIMUS_RECEIPT_MAX_POINTERS", raising=False)
    else:
        monkeypatch.setenv("INSPEXIMUS_RECEIPT_MAX_POINTERS", cap)
    cc.session_start({"hook_event_name": "SessionStart", "cwd": os.getcwd()})
    out = json.loads(capsys.readouterr().out)
    text = out["hookSpecificOutput"]["additionalContext"]
    start = text.index("[memory-index receipt]")
    end = text.find("\n[inspeximus]", start)
    return text[start:end if end > 0 else len(text)]


def test_session_start_injects_a_bounded_receipt(tmp_path, monkeypatch, capsys):
    got = _session_start_receipt(tmp_path, monkeypatch, capsys, 1000)
    assert len(got) <= 2000, len(got)
    assert got.count("\n  - ") == DEFAULT_MAX_POINTERS


def test_the_cap_can_be_changed_and_a_bad_value_falls_back(tmp_path, monkeypatch, capsys):
    assert _session_start_receipt(tmp_path, monkeypatch, capsys, 300, cap="5").count("\n  - ") == 5
    assert _session_start_receipt(tmp_path, monkeypatch, capsys, 300, cap="lots").count("\n  - ") == DEFAULT_MAX_POINTERS


def test_the_command_line_still_prints_every_pointer(tmp_path):
    idx = tmp_path / "MEMORY.md"
    idx.write_bytes(_index(300).encode())
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "inspeximus.memory_index_receipt", str(idx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and r.stdout.count("\n  - ") > DEFAULT_MAX_POINTERS
