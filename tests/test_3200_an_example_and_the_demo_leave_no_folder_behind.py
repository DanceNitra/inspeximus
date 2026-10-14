"""3.20 (the temp leftovers report, items 2 and 3): the erasure example and the demo do not leave folders behind silently.

THE DEFECTS. `examples/11_verifiable_erasure.py` made `inspeximus-erasure-*` in TEMP and never removed it: one folder
per run, holding a demo store and the receipt key it signed with (208 of them were in TEMP on 2026-10-14).
`inspeximus.demo.run_demo` removes its folder with `rmtree(ignore_errors=True)`, so a removal that failed left the
folder and said nothing.

WHAT IS PINNED HERE. The example removes its folder unless `--keep` is given, and says which it did. The demo reports a
folder it could not remove as `left_behind`, and its text output says so.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EXAMPLE = os.path.join(ROOT, "examples", "11_verifiable_erasure.py")


def _run_example(tmp_path, *args):
    env = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path))
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)
    proc = subprocess.run([sys.executable, EXAMPLE, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=600)
    left = [n for n in os.listdir(str(tmp_path)) if n.startswith("inspeximus-erasure-")]
    return proc, left


def test_the_erasure_example_removes_its_folder(tmp_path):
    proc, left = _run_example(tmp_path)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    assert left == [], "the example left %s in TEMP" % left
    assert "working directory removed" in proc.stdout


def test_the_erasure_example_keeps_its_folder_when_asked(tmp_path):
    proc, left = _run_example(tmp_path, "--keep")
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    assert len(left) == 1 and "left for inspection" in proc.stdout, "CONTROL: --keep keeps it"


def test_the_demo_says_when_it_could_not_remove_its_folder(monkeypatch):
    from inspeximus import demo
    if demo.cannot_run():
        pytest.skip(demo.cannot_run())
    real = demo.shutil.rmtree

    def failing(path, *a, **k):
        if os.path.basename(str(path)).startswith("inspeximus-demo-"):
            raise PermissionError(13, "held by another process", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(demo.shutil, "rmtree", failing)
    result = demo.run_demo()
    try:
        assert result["left_behind"] and os.path.isdir(result["left_behind"]), result.get("left_behind")
        assert "could not remove its working folder" in demo.render(result)
    finally:
        if result.get("left_behind"):
            real(result["left_behind"], ignore_errors=True)


def test_the_demo_reports_nothing_left_behind_after_a_normal_run():
    from inspeximus import demo
    if demo.cannot_run():
        pytest.skip(demo.cannot_run())
    result = demo.run_demo()
    assert result["left_behind"] is None and "could not remove" not in demo.render(result)
