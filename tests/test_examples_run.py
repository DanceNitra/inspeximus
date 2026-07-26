"""Every example the README points at must actually run.

Thirteen scripts under `examples/`, referenced from the README and the docs as "runnable", and nothing ran
them — not CI, not the suite. Measured before writing this: all thirteen pass, in 8 seconds total. So this
is not a fix, it is the thing that would have told us. An example is often the first code a reader
executes, and a broken one is a worse first impression than no example at all.

Each runs in its own subprocess, as a reader would run it: `python examples/NN_thing.py` from the repo
root, with a clean exit required. Where a script needs an optional dependency it is skipped by NAME with a
reason rather than dropped silently — a sweep that quietly skips is a sweep that stops covering.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")

#: Scripts that cannot run in a bare environment, each with the reason and what it needs. Named
#: deliberately: a skip nobody can see is indistinguishable from coverage.
NEEDS = {
    "07_langgraph_memory.py": "langgraph",
}


def _run(script):
    """Run an example the way a reader would -- but against THIS repository.

    Without PYTHONPATH the subprocess imports whatever pip happens to have installed. Locally that meant
    these tests passed against the published 1.78.0 while the working tree was never exercised at all; in
    CI, where nothing is installed, every example failed with ModuleNotFoundError. CI caught a test that
    was not testing the code in front of it, which is the worse of the two problems.
    """
    return subprocess.run(
        [sys.executable, os.path.join("examples", script)],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})


def _scripts():
    if not os.path.isdir(EXAMPLES):
        return []
    return sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".py") and not f.startswith("_"))


def test_the_examples_directory_is_not_empty():
    """If the directory is renamed or emptied, the parametrised test below would silently pass with zero
    cases -- a green sweep over nothing."""
    assert len(_scripts()) >= 10, _scripts()


@pytest.mark.parametrize("script", _scripts())
def test_example_runs_clean(script):
    dep = NEEDS.get(script)
    if dep:
        pytest.importorskip(dep, reason=f"{script} needs {dep}")

    r = _run(script)
    assert r.returncode == 0, (
        f"{script} exited {r.returncode}\n--- stdout tail ---\n{r.stdout[-1500:]}"
        f"\n--- stderr tail ---\n{r.stderr[-1500:]}")


@pytest.mark.parametrize("script", _scripts())
def test_example_prints_something(script):
    """A script that runs clean and says nothing teaches nothing. Every example here exists to show a
    result, so silence is a defect even though the exit code is zero."""
    if NEEDS.get(script):
        pytest.importorskip(NEEDS[script], reason=f"{script} needs {NEEDS[script]}")

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
