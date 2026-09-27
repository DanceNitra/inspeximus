"""tests/probe_shadow/sitecustomize.py: a probe the suite runs cannot rewrite a tracked file.

Built in a throwaway repository layout, so the test controls what counts as tracked and never touches
this checkout. The control runs the SAME script from outside probes/ and requires the real file to
change, so a shim that silently stopped loading could not pass.
"""
from __future__ import annotations

import os
import subprocess
import sys

SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_shadow")

SCRIPT = r'''
import json, os, pathlib, sys
root = sys.argv[1]
receipt = os.path.join(root, "probes", "x.result.json")
json.dump({"n": 2}, open(receipt, "w"))
print("readback", open(receipt).read())
pathlib.Path(root, "data", "bench.json").write_text("new", encoding="utf-8")
tmp = os.path.join(root, "probes", "tmp.json")
open(tmp, "w").write("replaced")
os.replace(tmp, os.path.join(root, "data", "atomic.json"))
open(os.path.join(root, "probes", "untracked.json"), "w").write("mine")
'''


def _layout(tmp_path):
    root = tmp_path / "repo"
    (root / "probes").mkdir(parents=True)
    (root / "data").mkdir()
    for rel, body in (("probes/x.result.json", '{"n": 1}'), ("data/bench.json", "old"), ("data/atomic.json", "old")):
        (root / rel).write_text(body, encoding="utf-8")
    listing = tmp_path / "tracked.txt"
    listing.write_text("probes/x.result.json\ndata/bench.json\ndata/atomic.json\n", encoding="utf-8")
    return root, listing


def _run(script_path, root, listing, shadow):
    env = {**os.environ, "PYTHONPATH": SHIM, "INSPEXIMUS_PROBE_ROOT": str(root),
           "INSPEXIMUS_PROBE_SHADOW": str(shadow), "INSPEXIMUS_PROBE_TRACKED": str(listing)}
    return subprocess.run([sys.executable, str(script_path), str(root)], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def test_a_probe_writes_tracked_files_into_the_shadow_and_reads_them_back(tmp_path):
    root, listing = _layout(tmp_path)
    probe = root / "probes" / "p.py"
    probe.write_text(SCRIPT, encoding="utf-8")
    shadow = tmp_path / "shadow"
    r = _run(probe, root, listing, shadow)
    assert r.returncode == 0, r.stderr
    assert (root / "probes" / "x.result.json").read_text() == '{"n": 1}', "the committed receipt is untouched"
    assert (root / "data" / "bench.json").read_text() == "old"
    assert (root / "data" / "atomic.json").read_text() == "old", "os.replace onto a tracked file is shadowed"
    assert (shadow / "probes" / "x.result.json").read_text() == '{"n": 2}'
    assert (shadow / "data" / "atomic.json").read_text() == "replaced"
    assert 'readback {"n": 2}' in r.stdout, "the probe reads back what it wrote"
    assert (root / "probes" / "untracked.json").read_text() == "mine", "an untracked file is written normally"


def test_control_the_same_script_outside_probes_writes_the_real_files(tmp_path):
    root, listing = _layout(tmp_path)
    script = root / "p.py"                                  # not in probes/: the shim must stay out of it
    script.write_text(SCRIPT, encoding="utf-8")
    r = _run(script, root, listing, tmp_path / "shadow")
    assert r.returncode == 0, r.stderr
    assert (root / "probes" / "x.result.json").read_text() == '{"n": 2}'
    assert (root / "data" / "atomic.json").read_text() == "replaced"
    assert not (tmp_path / "shadow").exists()


def test_the_suite_turns_the_shadow_on_for_its_children():
    """The fixture in conftest.py is what makes the shim reach every probe the suite starts."""
    assert SHIM in os.environ.get("PYTHONPATH", "").split(os.pathsep)
    for k in ("INSPEXIMUS_PROBE_ROOT", "INSPEXIMUS_PROBE_SHADOW", "INSPEXIMUS_PROBE_TRACKED"):
        assert os.environ.get(k), k
    assert "probes/_receipt.py" in open(os.environ["INSPEXIMUS_PROBE_TRACKED"], encoding="utf-8").read()
