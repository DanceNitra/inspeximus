# -*- coding: utf-8 -*-
"""A test importing an extra CI does not install must guard it, or the whole file errors on collection.

This rule has been written down three times and broken three times. Writing it a fourth time is not
the fix; the fix is a check that runs on every push and cannot be forgotten. It is the smallest
member of a class we measured on 2026-08-27: of 453 notes in the project memory, 104 carry a
repeat marker, and the one rule that never got broken this year is the one wired into a hook that
refuses. Prose does not hold. Checks do.

WHY THE ALLOW-LIST IS DERIVED AND NOT WRITTEN. A hand-listed set of "optional" modules was wrong the
first time it was tried here: it counted `cryptography` as optional, so it flagged two files that are
fine because ci.yml installs cryptography explicitly. The set is therefore computed as

    (modules declared under [project.optional-dependencies])  minus  (anything a CI job pip-installs)

so it moves on its own when either file changes, and neither can drift away from the other silently.

THE FAILURE DIRECTION IS DELIBERATE. Distribution names and import names differ (llama-index-core
imports as llama_index, google-adk as google.adk), and the mapping below is a prefix match rather
than a lookup table. Where it is incomplete the check UNDER-reports, which is the safe half: it can
miss a new unguarded import, and it cannot fail a file that is already correct. A false alarm here
would be worse than a miss, because a guard that cries wolf is a guard somebody switches off.
"""
import io
import os
import re

import pytest

# THIS FILE BROKE ITS OWN RULE ON THE FIRST PUSH, and CI caught it in five minutes.
#
# It imported `tomllib` at module scope. That is stdlib only from Python 3.11, the matrix runs 3.9,
# and the whole module errored at collection: exactly the failure it exists to prevent, in the file
# that enforces it. The lesson is not that the rule is hard to follow. It is that the author of a
# rule is not exempt from it, and only the machine noticed.
#
# The guard is a fallback rather than a skip, so the check still runs everywhere it can: tomllib on
# 3.11+, tomli where it is installed, and a skip only when neither exists.
try:                                      # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:               # pragma: no cover - older interpreters
    try:
        import tomli as _toml
    except ModuleNotFoundError:
        _toml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.M)


def _ci_installs():
    p = os.path.join(ROOT, ".github", "workflows", "ci.yml")
    text = io.open(p, encoding="utf-8").read()
    out = set()
    for m in re.finditer(r"pip install ([^\n]+)", text):
        for tok in m.group(1).split():
            if tok.startswith(("-", ".", '"')) or tok == "pip":
                continue
            out.add(re.split(r"[<>=\[]", tok)[0].replace("-", "_").lower())
    return out


def _extras():
    if _toml is None:
        pytest.skip("no TOML reader on this interpreter (tomllib is 3.11+, tomli not installed)")
    d = _toml.load(io.open(os.path.join(ROOT, "pyproject.toml"), "rb"))
    out = set()
    for specs in (d["project"].get("optional-dependencies") or {}).values():
        for spec in specs:
            out.add(re.split(r"[<>=\[ ]", spec)[0].replace("-", "_").lower())
    return out


def _must_guard():
    """Extras no CI job installs, as import-name prefixes."""
    names = _extras() - _ci_installs()
    # llama_index_core is imported as llama_index, google_adk as google.adk, autogen_core as autogen
    return {n.split("_")[0] if n.split("_")[0] in {"llama", "google", "autogen", "openai"} else n
            for n in names}


def test_the_derived_set_is_not_empty():
    """CONTROL. If pyproject or ci.yml is renamed or reshaped, the set below silently empties and
    every assertion in this file passes without examining anything. That is the failure this whole
    repository keeps finding elsewhere; it does not get to happen here."""
    guarded = _must_guard()
    assert guarded, "the must-guard set is empty, so this file would pass vacuously"
    assert "mcp" in guarded, "mcp is an extra and CI does not install it; the derivation is wrong"
    assert "cryptography" not in guarded, (
        "cryptography IS installed by ci.yml, so requiring a guard for it would be a false alarm")


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(TESTS) if f.endswith(".py")))
def test_an_unguarded_extra_import_would_error_on_collection(name):
    text = io.open(os.path.join(TESTS, name), encoding="utf-8", errors="replace").read()
    tops = {m.split(".")[0] for m in IMPORT.findall(text)}
    needs = sorted(tops & _must_guard())
    if not needs:
        pytest.skip("imports no extra that CI omits")
    assert "importorskip" in text, (
        "%s imports %s, which CI does not install, and does not call pytest.importorskip. "
        "Without the guard the whole file errors at collection and every test in it is lost, "
        "silently, because a collection error is not a failed assertion." % (name, ", ".join(needs)))
