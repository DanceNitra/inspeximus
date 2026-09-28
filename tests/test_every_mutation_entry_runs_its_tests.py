"""Every entry in tools/mutations.json runs at least one test, under the gate's own pytest call (A-24).

pytest.ini deselects the `mutation` marker by default. Nine entries listed only marked tests, so the gate's
pre-flight collected nothing, pytest exited 5, and the gate reported "tests are not green before mutating":
they were skipped on every machine, and no full run could exit 0 (RELEASING.md requires 0 skipped). The gate
now lifts the marker filter for such an entry, and this file proves, for the committed spec, that each
entry's listed tests collect under exactly the selection the gate uses.

A file that skips at import because an optional dependency is missing (`pytest.importorskip("mcp")`)
collects nothing on a machine without that extra, and nothing else can be said about it there. Such an
entry is named "not collectable here", not "broken"; a job with the extras installed checks it.
"""
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mutation_check  # noqa: E402


_IMPORT_SKIP = re.compile(r"SKIPPED \[\d+\] (.+?):\d+: could not import ")


def _collected(files, lift, cwd=ROOT):
    """The ids the gate's selection collects from `files`, and the files that skipped at import for a
    missing module."""
    args = [sys.executable, "-m", "pytest", *files, "--collect-only", "-q", "-rs", "-p", "no:randomly",
            "-n", "0"]
    if lift:
        args += ["-m", ""]
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    lines = [ln.strip().replace("\\", "/") for ln in r.stdout.splitlines()]
    ids = {ln for ln in lines if "::" in ln and not ln.startswith("SKIPPED")}
    skipped = {m.group(1) for m in map(_IMPORT_SKIP.match, lines) if m}
    return ids, skipped


def _classify(lists, marked, default, lifted):
    """(broken, not_here): entries whose listed tests collect nothing. An entry is not collectable here
    only when EVERY file it lists skipped at import; any other empty entry is broken."""
    broken, not_here = [], []
    for t in sorted(lists):
        ids, skipped = lifted if t in marked else default
        if any(_selects(x, ids) for x in t):
            continue
        files = {x.split("::", 1)[0].replace("\\", "/") for x in t}
        (not_here if files <= skipped else broken).append(t)
    return broken, not_here


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
    default = _collected(files, lift=False)
    lifted = _collected(sorted({x.split("::", 1)[0] for t in marked for x in t}), lift=True)
    broken, not_here = _classify(lists, marked, default, lifted)
    if len(not_here) > len(lists) // 2:
        raise AssertionError(f"control: {len(not_here)} of {len(lists)} entries cannot be collected on this "
                             f"machine, so this run checked too little to pass: {not_here[:5]}")
    assert not broken, f"{len(broken)} entries' listed tests collect nothing under the gate: {broken[:5]}"


def test_a_missing_extra_is_told_apart_from_an_empty_entry(tmp_path):
    """CONTROL: an entry whose file skips at import is not collectable here; an entry whose file defines
    no test, alone or beside such a file, is still broken."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_extra.py").write_text(
        'import pytest\npytest.importorskip("no_such_module_a24")\ndef test_a(): pass\n', encoding="utf-8")
    (tmp_path / "test_empty.py").write_text("# a test file that defines no test\n", encoding="utf-8")
    (tmp_path / "test_fine.py").write_text("def test_b(): pass\n", encoding="utf-8")
    lists = {("test_extra.py",), ("test_empty.py",), ("test_fine.py::test_b",),
             ("test_extra.py", "test_empty.py")}
    got = _collected(sorted({x.split("::", 1)[0] for t in lists for x in t}), lift=False, cwd=str(tmp_path))
    assert got[1] == {"test_extra.py"}, got
    broken, not_here = _classify(lists, set(), got, got)
    assert not_here == [("test_extra.py",)], not_here
    assert broken == [("test_empty.py",), ("test_extra.py", "test_empty.py")], broken


def test_without_the_marker_lift_the_harness_tests_collect_nothing():
    """CONTROL: the defect this file guards is real in the repo's own config."""
    tests = ["tests/test_mutation_restore_is_byte_exact.py"]
    assert mutation_check._marked_mutation(tests)
    assert not _collected(tests, lift=False)[0]
    assert _collected(tests, lift=True)[0]


def test_exit_5_is_named_as_an_empty_selection_not_as_red(tmp_path):
    spec = [{"name": "an entry whose test selection is empty", "file": "inspeximus/core.py",
             "old": "def _first_unencodable(value, path: str):", "new": "def _first_unencodable(value, path):",
             "tests": [str(tmp_path / "test_nothing_here.py")]}]
    (tmp_path / "test_nothing_here.py").write_text("# a test file that defines no test", encoding="utf-8")
    # Its own ini makes tmp_path the rootdir; without one pytest 9 lists every ancestor of the file,
    # all 131,179 entries of TEMP included (see test_a_skipped_test_is_not_a_survivor.py).
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
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
