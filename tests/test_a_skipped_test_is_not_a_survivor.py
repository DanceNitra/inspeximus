"""A mutant whose listed tests did not run is NOT EVALUATED, never "survived" (A-26).

PC2's full run at 97853c5f reported three docs/verify/index.html mutants as SURVIVED. Their killing test,
`test_the_page_agrees_on_every_case`, needs the Playwright `page` fixture; without it the test SKIPS, the
file's other tests pass, pytest exits 0, and the gate read that as "no test noticed". The mutant was
never shown to a test that could see it. The same gate also accepted a pre-flight in which every listed
test skipped, then mutated the source and ran nothing against it.

Every entry here mutates a file in tmp_path, never a file the rest of the suite imports.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATCHES = "def test_catches():\n    assert open(TARGET, encoding='utf-8').read().count('VALUE = 1') == 1\n"
MISSES = "def test_misses():\n    assert True\n"
SKIPS = ("import pytest\n\n\n@pytest.mark.skip(reason='needs a browser this machine does not have')\n"
         "def test_skips():\n    assert False\n")


def _gate(tmp_path, *bodies):
    """Run the real gate on one entry whose listed test file holds `bodies`; return its output."""
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    # Its own ini makes tmp_path the rootdir. Without one, pytest 9 lists every ancestor of a file
    # outside the repo, and TEMP held 131,179 entries: 70 to 100 s per call, and a collection error
    # whenever another process removed a folder during the scan. Measured: 75 s -> 4.2 s.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    test = tmp_path / "test_listed.py"
    test.write_text(f"TARGET = {str(target)!r}\n\n\n" + "\n\n".join(bodies), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{"name": "one entry", "file": str(target), "old": "VALUE = 1",
                                 "new": "VALUE = 2", "tests": [str(test)]}]), encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join("tools", "mutation_check.py"), str(spec)], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTEST_ADDOPTS": "-n 0"})
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n", "the gate must restore the target"
    return r


def test_a_mutant_whose_catching_test_skipped_is_not_evaluated(tmp_path):
    """The Playwright shape: one listed test passes, the one that would notice is skipped."""
    r = _gate(tmp_path, MISSES, SKIPS)
    assert r.returncode != 0, r.stdout
    assert "SURVIVES" not in r.stdout and "SURVIVED" not in r.stdout, r.stdout
    assert "NOT EVALUATED" in r.stdout and "needs a browser" in r.stdout, r.stdout


def test_a_pre_flight_in_which_nothing_ran_is_not_green(tmp_path):
    r = _gate(tmp_path, SKIPS)
    assert r.returncode != 0, r.stdout
    assert "SURVIVES" not in r.stdout and "killed by" not in r.stdout, r.stdout
    assert "no listed test ran" in r.stdout and "needs a browser" in r.stdout, r.stdout


def test_control_a_real_survivor_is_still_called_one(tmp_path):
    """CONTROL: the fix must not rename a mutant that every listed test saw and missed."""
    r = _gate(tmp_path, MISSES)
    assert r.returncode != 0 and "SURVIVES" in r.stdout, r.stdout


def test_control_a_kill_beside_a_skip_is_still_a_kill(tmp_path):
    """CONTROL: a test that ran and failed is evidence, whatever else skipped."""
    r = _gate(tmp_path, CATCHES, SKIPS)
    assert r.returncode == 0 and "killed by test_catches" in r.stdout, r.stdout
