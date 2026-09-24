"""Every example the README points at must actually run.

Thirteen scripts under `examples/`, referenced from the README and the docs as "runnable", and nothing ran
them — not CI, not the suite. Measured before writing this: all thirteen pass, in 8 seconds total. So this
is not a fix, it is the thing that would have told us. An example is often the first code a reader
executes, and a broken one is a worse first impression than no example at all.

Each runs in its own subprocess, as a reader would run it: `python examples/NN_thing.py` from the repo
root, with a clean exit required. Where a script needs an optional dependency it is skipped by NAME with a
reason rather than dropped silently — a sweep that quietly skips is a sweep that stops covering.

A script that needs an extra SAYS SO IN ITS FIRST LINES, where a reader decides whether they can run it:
`pip install "inspeximus[crypto]"` within the first HEADER_LINES lines. NEEDS is read from there, so the
declaration a reader sees and the one this sweep enforces are the same line.

With INSPEXIMUS_PYTHON set to an interpreter that has inspeximus INSTALLED (a clean venv), every example
runs against that installation instead of this checkout: a copy of the script in an empty folder, no
PYTHONPATH, a HOME of its own. The `readme-and-examples` CI job does this twice, once after a plain
`pip install inspeximus` and once after `pip install "inspeximus[crypto]"`.
"""
import os
import re
import shutil
import subprocess
import tempfile
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
sys.path.insert(0, os.path.join(ROOT, "tools"))
from readme_blocks import EXTRA_MODULE, extras_declared, has_module, isolated_env  # noqa: E402

#: The interpreter under test: None is this one, importing this checkout.
PYTHON = os.environ.get("INSPEXIMUS_PYTHON") or None

#: How far down a script its install line may sit and still count as "in its first lines".
HEADER_LINES = 10


def _scripts():
    if not os.path.isdir(EXAMPLES):
        return []
    return sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".py") and not f.startswith("_"))


def _declared_extras(script):
    """The extras a script names as `inspeximus[extra]` in its first HEADER_LINES lines."""
    with open(os.path.join(EXAMPLES, script), encoding="utf-8") as fh:
        head = "".join(fh.readline() for _ in range(HEADER_LINES))
    extras = extras_declared(head)
    unknown = [e for e in extras if e not in EXTRA_MODULE]
    assert not unknown, (f"{script} declares inspeximus{unknown}; add the module each extra brings to "
                         f"EXTRA_MODULE in tools/readme_blocks.py")
    return extras


#: Scripts that cannot run in a bare environment, with the modules they need, READ FROM EACH SCRIPT'S
#: FIRST LINES. Named deliberately: a skip nobody can see is indistinguishable from coverage.
#:
#: The five that sign something were absent from the hand-kept map this replaced until 2.0.2, and the
#: reason is the shape this repository keeps rediscovering: the sweep runs under `sys.executable`, the
#: developer's interpreter, where `cryptography` is always installed because it is a TEST dependency.
#: So those five passed here while failing for any reader who ran `pip install inspeximus` and followed
#: the README. The check ran in the one environment where the defect could not occur. And the map lived
#: here, where no reader looks: two of the five still told the reader `pip install inspeximus`, one named
#: no install at all, and one named it on line 18.
NEEDS = {s: [EXTRA_MODULE[e] for e in _declared_extras(s)] for s in _scripts() if _declared_extras(s)}

#: What a reader must actually type. Every module in NEEDS has to be installable by one of these, or the
#: skip reason names something nobody can act on.
INSTALL_HINT = {module: 'pip install "inspeximus[%s]"' % extra for extra, module in EXTRA_MODULE.items()}


def _require(script):
    """Skip, by name and with the install line, when the interpreter under test lacks what the script
    declares. The base-install sweep below still runs it, and requires it to fail naming the module."""
    for dep in NEEDS.get(script, []):
        if PYTHON is None:
            pytest.importorskip(dep, reason=f"{script} needs {dep}: {INSTALL_HINT[dep]}")
        elif not has_module(dep, PYTHON):
            pytest.skip(f"{script} needs {dep}: {INSTALL_HINT[dep]}")


def _run_installed(path, bootstrap=None):
    """Run a script the way a reader who ran `pip install` would: a copy in an empty folder, run with
    the interpreter under test, no PYTHONPATH, and a HOME of its own.

    A COPY, not the file in place. Run from examples/, 12_split_view_detection.py puts the checkout next
    to it first on sys.path and 11_verifiable_erasure.py hands that checkout to its subprocesses, so both
    would pass against this repository instead of the installation being tested."""
    base = tempfile.mkdtemp(prefix="inspeximus_installed_")
    work, home = os.path.join(base, "work"), os.path.join(base, "home")
    os.makedirs(work)
    os.makedirs(home)
    shutil.copy(path, work)
    argv = [PYTHON] + (["-c", bootstrap] if bootstrap else []) + [os.path.basename(path)]
    return subprocess.run(argv, cwd=work, env=isolated_env(home, PYTHON), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)


def _run(script):
    """Run an example the way a reader would -- but against THIS repository.

    Without PYTHONPATH the subprocess imports whatever pip happens to have installed. Locally that meant
    these tests passed against the published 1.78.0 while the working tree was never exercised at all; in
    CI, where nothing is installed, every example failed with ModuleNotFoundError. CI caught a test that
    was not testing the code in front of it, which is the worse of the two problems.
    """
    if PYTHON:
        return _run_installed(os.path.join(EXAMPLES, script))
    # cwd is a TEMP DIRECTORY, not the repo. The examples persist to relative paths, so running them here
    # appended to the tracked `memory.json` on every single suite run: 85 runs, 340 records, 337 superseded,
    # committed. Imports still resolve because PYTHONPATH points at the repo -- only the demo's OUTPUT moves.
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "examples", script)],
        cwd=tempfile.mkdtemp(prefix="inspeximus_example_"), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})


def test_the_examples_directory_is_not_empty():
    """If the directory is renamed or emptied, the parametrised test below would silently pass with zero
    cases -- a green sweep over nothing."""
    assert len(_scripts()) >= 10, _scripts()


@pytest.mark.parametrize("script", _scripts())
def test_example_runs_clean(script):
    _require(script)

    r = _run(script)
    assert r.returncode == 0, (
        f"{script} exited {r.returncode}\n--- stdout tail ---\n{r.stdout[-1500:]}"
        f"\n--- stderr tail ---\n{r.stderr[-1500:]}")


@pytest.mark.parametrize("script", _scripts())
def test_example_prints_something(script):
    """A script that runs clean and says nothing teaches nothing. Every example here exists to show a
    result, so silence is a defect even though the exit code is zero."""
    _require(script)

    r = _run(script)
    assert r.stdout.strip(), f"{script} produced no output"


def test_every_example_referenced_in_the_docs_exists():
    """The other direction: a README that points at a deleted example sends the reader to a 404."""
    import re

    missing = []
    for rel in ["README.md"] + [os.path.join("docs", f) for f in
                                (os.listdir(os.path.join(ROOT, "docs"))
                                 if os.path.isdir(os.path.join(ROOT, "docs")) else [])
                                if f.endswith(".md")]:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"examples/([\w.]+\.py)", text):
            if not os.path.exists(os.path.join(EXAMPLES, m.group(1))):
                missing.append(f"{os.path.basename(rel)} -> examples/{m.group(1)}")
    assert not missing, "documentation points at examples that do not exist: " + "; ".join(sorted(set(missing)))


def test_every_local_link_and_image_in_the_docs_resolves():
    """24 were broken when this was written, and 22 of them were the same mistake: a link inside `docs/`
    written as if from the repo root, so `probes/x.py` resolved to `docs/probes/x.py` and 404'd on GitHub.
    The files existed the whole time — only the path was wrong, which is why nobody noticed locally.

    The other two pointed at a demo GIF and tape that never existed in this repository at all.

    Relative to each file's OWN directory, as a browser and GitHub both resolve them."""
    import re

    files = ["README.md"]
    docs = os.path.join(ROOT, "docs")
    if os.path.isdir(docs):
        files += [os.path.join("docs", f) for f in sorted(os.listdir(docs)) if f.endswith(".md")]

    missing = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        base = os.path.dirname(path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"!?\[[^\]]*\]\(([^)#]+)\)", text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, target))):
                missing.append(f"{rel} -> {target}")
    assert not missing, "documentation links that do not resolve: " + "; ".join(sorted(set(missing)))


# ── the demo must not accumulate, and must not write into the repository ────────────────────────────
def test_the_flagship_demo_shows_one_correction_not_a_pile():
    """`01_basics.py` is the first code most readers run, and its subject is "a correction stays
    corrected". It persists to a RELATIVE path, and the suite used to run it with cwd=<repo>, so every run
    appended to the tracked `memory.json`: 85 runs, 340 records, 337 of them superseded, committed. On a
    fresh clone the history section printed dozens of alternating duplicates before the one active line --
    the exact opposite of what the example exists to show."""
    r = _run("01_basics.py")
    assert r.returncode == 0, r.stderr[-800:]

    lines = [ln.strip() for ln in r.stdout.splitlines()]
    superseded = [ln for ln in lines if ln.startswith("superseded")]
    active = [ln for ln in lines if ln.startswith("active")]
    assert len(superseded) == 1, f"one correction means ONE superseded line, got {len(superseded)}"
    assert len(active) == 1, f"and exactly one current value, got {len(active)}"


def test_running_the_examples_does_not_dirty_the_repository():
    """The residue reached the repo because the runner used cwd=ROOT. A demo that writes into the working
    tree turns every CI run into a commit-shaped change nobody chose."""
    def dirty():
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        return {ln[3:] for ln in out.splitlines() if ln.strip()}

    before = dirty()
    _run("01_basics.py")
    after = dirty()
    # ONLY NEW DIRT COUNTS. Comparing the two snapshots for equality made this a race: `git status`
    # reads the whole working tree, other xdist workers rewrite probe result files throughout the run,
    # and a file that went from modified to clean between the two reads failed the test for something
    # the example cannot do. Observed on the 3.9 leg, where the extra entry was present BEFORE and gone
    # after. The guarantee worth keeping is one-directional: the example must not dirty anything that
    # was clean.
    appeared = after - before
    assert not appeared, (
        "running an example dirtied tracked files: %s" % sorted(appeared))


def test_the_demo_store_is_not_a_tracked_file():
    """It is generated output. Committing it meant a clone started with someone else's 340 records."""
    tracked = subprocess.run(["git", "ls-files", "memory.json"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert not tracked, "memory.json is generated by the demo and must not be committed"


# ── the base-install sweep ───────────────────────────────────────────────────────────────────────
# The sweep above runs under `sys.executable`, which is the developer's interpreter and the CI test
# leg, and BOTH have `cryptography` installed because it is a test dependency of this repository. So
# five examples that a reader cannot run passed here for months. `pip install inspeximus` gives a
# package with ZERO dependencies by design; the check has to reproduce that, not the developer's box.
#
# Blocking the imports is deliberate rather than building a clean virtualenv per example: it costs
# milliseconds instead of minutes, it cannot be defeated by whatever the CI image happens to ship, and
# it is the same technique the zero-dependency guard in the release checklist already uses.

#: Everything outside the standard library that a base install does NOT get.
BASE_BLOCKED = ("cryptography", "yaml", "numpy", "mcp", "langgraph", "langchain", "langchain_core",
                "llama_index", "autogen", "autogen_core", "google", "openai", "pydantic_ai",
                "crewai", "haystack")

_BOOTSTRAP = """
import sys, runpy
BLOCK = {blocked!r}
class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCK:
            raise ImportError('blocked for the base-install sweep: ' + name)
        return None
sys.meta_path.insert(0, _Blocker())
runpy.run_path(sys.argv[1], run_name='__main__')
"""


def _run_base_only(path):
    """Run a script as if only `pip install inspeximus` had been done."""
    if PYTHON:
        return _run_installed(path, bootstrap=_BOOTSTRAP.format(blocked=BASE_BLOCKED))
    return subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP.format(blocked=BASE_BLOCKED), path],
        cwd=tempfile.mkdtemp(prefix="inspeximus_base_"), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})


@pytest.mark.parametrize("script", sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".py")))
def test_an_example_either_runs_on_a_base_install_or_says_what_it_needs(script):
    """The property a reader actually depends on.

    Either the example runs after `pip install inspeximus`, or its first lines name the extra, the extra
    is real, and the failure mentions the module so the reader is not left guessing."""
    r = _run_base_only(os.path.join(EXAMPLES, script))
    if r.returncode == 0:
        assert script not in NEEDS, (
            f"{script} runs on a base install but its first lines declare it needs {NEEDS[script]!r}; a "
            f"stale declaration turns into a skip, and a skip nobody can see is indistinguishable from "
            f"coverage")
        return
    deps = NEEDS.get(script)
    assert deps, (
        f"{script} fails on a plain `pip install inspeximus` and declares nothing. Put the install line, "
        f'e.g. `pip install "inspeximus[crypto]"`, in its first {HEADER_LINES} lines, or make the example '
        f"base-safe.\nstderr tail: {r.stderr[-400:]}")
    for dep in deps:
        assert dep in (r.stderr + r.stdout), (
            f"{script} is declared to need {dep!r} but its failure never mentions it, so the reader cannot "
            f"act on it.\nstderr tail: {r.stderr[-400:]}")
        assert dep in INSTALL_HINT, f"nothing tells a reader how to install {dep!r}"


def _optional_dependencies():
    """`[project.optional-dependencies]` without tomllib, which is 3.11+ while this project supports 3.9.

    The first version imported tomllib and passed locally and on two of the three CI legs; 3.9 failed with
    ModuleNotFoundError. Adding `tomli` would put a dependency into the test suite of a package whose
    entire pitch is that it has none, so the section is parsed directly. It is a flat table of
    `name = ["req", ...]` lines, which is worth exactly these ten lines and no library."""
    out, current = {}, None
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("["):
                current = stripped
                continue
            if (current != "[project.optional-dependencies]" or "=" not in stripped
                    or stripped.startswith("#")):
                continue     # a comment inside the table also contains "="; it is not an extra
            name, _, rest = stripped.partition("=")
            reqs = re.findall(r'"([^"]+)"', rest)
            if reqs:
                out[name.strip()] = reqs
    assert out, "parsed no extras at all from pyproject.toml; the parser or the file moved"
    return out


def test_every_declared_dependency_maps_to_a_real_extra():
    """`pip install "inspeximus[crypto]"` has to exist. Before 2.0.2 it did not: pip accepts an unknown
    extra, installs nothing, and the example fails exactly as before -- the most confusing outcome."""
    extras = _optional_dependencies()
    for dep, hint in INSTALL_HINT.items():
        name = hint.split("[", 1)[1].split("]", 1)[0]
        assert name in extras, f"install hint for {dep!r} names extra {name!r}, which pyproject does not define"
        assert any(dep in req for req in extras[name]), (
            f"extra {name!r} exists but does not pull in {dep!r}: {extras[name]}")


def test_control_the_base_sweep_can_see_a_missing_dependency(tmp_path):
    """Without this the sweep above is satisfied by a blocker that blocks nothing."""
    good = tmp_path / "base_safe.py"
    good.write_text("print('runs with the standard library alone')\n", encoding="utf-8")
    assert _run_base_only(str(good)).returncode == 0, "the blocker rejects a script that needs nothing"

    bad = tmp_path / "needs_crypto.py"
    bad.write_text("import cryptography\nprint('should never get here')\n", encoding="utf-8")
    r = _run_base_only(str(bad))
    assert r.returncode != 0, "a script importing cryptography ran under the base sweep; nothing is blocked"
    assert "cryptography" in (r.stderr + r.stdout)
