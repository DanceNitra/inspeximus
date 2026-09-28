"""Every entry in tools/mutations.json runs at least one test, under the gate's own pytest call (A-24).

pytest.ini deselects the `mutation` marker by default. Nine entries listed only marked tests, so the gate's
pre-flight collected nothing, pytest exited 5, and the gate reported "tests are not green before mutating":
they were skipped on every machine, and no full run could exit 0 (RELEASING.md requires 0 skipped). The gate
now lifts the marker filter for such an entry, and this file proves, for the committed spec, that each
entry's listed tests collect under exactly the selection the gate uses.
"""
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mutation_check  # noqa: E402


def _collected(files, lift):
    args = [sys.executable, "-m", "pytest", *files, "--collect-only", "-q", "-p", "no:randomly", "-n", "0"]
    if lift:
        args += ["-m", ""]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    return {ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if "::" in ln}


def _selects(item, ids):
    item = item.replace("\\", "/")
    return any(i == item or i.startswith(item + "::") or i.startswith(item + "[") for i in ids)


def test_every_entrys_listed_tests_collect_under_the_gates_selection():
    with io.open(os.path.join(ROOT, "tools", "mutations.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    lists = {tuple(e["tests"] if isinstance(e["tests"], list) else [e["tests"]]) for e in spec}
    marked = {t for t in lists if mutation_check._marked_mutation(list(t))}
    if not marked:
        raise AssertionError("control: no entry lists a mutation-marked test, so the marker case is not exercised")
    files = sorted({x.split("::", 1)[0] for t in lists for x in t})
    default_ids = _collected(files, lift=False)
    lifted_ids = _collected(sorted({x.split("::", 1)[0] for t in marked for x in t}), lift=True)
    empty = [t for t in sorted(lists)
             if not any(_selects(x, lifted_ids if t in marked else default_ids) for x in t)]
    assert not empty, f"{len(empty)} entries' listed tests collect nothing under the gate: {empty[:5]}"


def test_without_the_marker_lift_the_harness_tests_collect_nothing():
    """CONTROL: the defect this file guards is real in the repo's own config."""
    tests = ["tests/test_mutation_restore_is_byte_exact.py"]
    assert mutation_check._marked_mutation(tests)
    assert not _collected(tests, lift=False)
    assert _collected(tests, lift=True)


def test_exit_5_is_named_as_an_empty_selection_not_as_red(tmp_path):
    spec = [{"name": "an entry whose test selection is empty", "file": "inspeximus/core.py",
             "old": "def _first_unencodable(value, path: str):", "new": "def _first_unencodable(value, path):",
             "tests": [str(tmp_path / "test_nothing_here.py")]}]
    (tmp_path / "test_nothing_here.py").write_text("# a test file that defines no test", encoding="utf-8")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join("tools", "mutation_check.py"), str(p)], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTEST_ADDOPTS": "-n 0"})
    assert r.returncode != 0, r.stdout
    assert "collect nothing" in r.stdout, r.stdout


def test_the_gates_own_call_runs_a_marked_file():
    """The pytest call the gate makes, not a copy of it: a marked file's tests must run and pass."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT}
    r = mutation_check._pytest(["tests/test_mutation_restore_is_byte_exact.py"], env)
    assert r.returncode == 0 and " passed" in r.stdout, (r.returncode, r.stdout[-400:])
