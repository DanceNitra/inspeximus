"""A path on another drive never crashes a product relpath (Windows CI trial, 2026-09-28).

os.path.relpath raises ValueError across drives on Windows. Each product site gets the ValueError forced
here, on every OS, and must fall back; a control shows the same-drive answer is unchanged.
"""
import os

import pytest


def _cross_drive(path, start=None):
    raise ValueError("path is on mount 'C:', start on mount 'D:'")


def test_the_helper_falls_back_to_the_absolute_path(monkeypatch, tmp_path):
    from inspeximus._surface import relpath_or_abs
    inside = os.path.join(str(tmp_path), "a", "b.json")
    assert relpath_or_abs(inside, str(tmp_path)) == os.path.join("a", "b.json")          # control
    monkeypatch.setattr(os.path, "relpath", _cross_drive)
    assert relpath_or_abs(inside, str(tmp_path)) == os.path.abspath(inside)


def test_the_compliance_evidence_survives_a_receipt_on_another_drive(monkeypatch, tmp_path):
    from inspeximus import compliance
    base = compliance.robustness_evidence(probes_dir=str(tmp_path))                        # control
    monkeypatch.setattr(os.path, "relpath", _cross_drive)
    crossed = compliance.robustness_evidence(probes_dir=str(tmp_path))
    assert len(crossed.get("rows") or []) == len(base.get("rows") or []) > 0, crossed


def test_the_hook_shows_a_file_on_another_drive(monkeypatch):
    from inspeximus import claude_code
    assert claude_code._rel(os.path.join(os.getcwd(), "x.py"), os.getcwd()) == "x.py"      # control
    monkeypatch.setattr(os.path, "relpath", _cross_drive)
    assert claude_code._rel("D:/proj/x.py", "C:/work") == "D:/proj/x.py"
