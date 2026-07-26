"""Nothing may use syntax newer than the Python floor this package declares.

`pyproject.toml` says which Pythons are supported and CI runs the oldest of them. This machine runs 3.13,
so the whole local suite can be green while an import fails on 3.9 -- which is exactly what happened: a
`-> str | None` annotation in a test module raised `TypeError: unsupported operand type(s) for |` at
COLLECTION time on 3.9, taking the entire run down, an hour after I had shipped a fix for a different
"local green is not CI green" defect.

PEP 604 (`X | Y`) is evaluated at runtime in an annotation unless the module opts into postponed
evaluation with `from __future__ import annotations`. That is the trap: the syntax parses everywhere, so
`py_compile` and a local run both say fine. Only executing the import on the old interpreter fails, and
only CI does that.

This checks it statically, on every interpreter, in under a second.
"""
import ast
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _floor():
    """The oldest Python the package claims to support, as (major, minor)."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        m = re.search(r'requires-python\s*=\s*"[^0-9]*(\d+)\.(\d+)', fh.read())
    assert m, "pyproject declares no requires-python; the floor this file enforces is undefined"
    return int(m.group(1)), int(m.group(2))


FLOOR = _floor()


def _sources():
    out = []
    for sub in ("inspeximus", "tests", "tools", "examples"):
        d = os.path.join(ROOT, sub)
        for base, _, files in os.walk(d) if os.path.isdir(d) else []:
            if "__pycache__" in base:
                continue
            out += [os.path.join(base, f) for f in files if f.endswith(".py")]
    return sorted(out)


def _pep604_in_annotations(tree):
    """Every `X | Y` that sits in a position Python evaluates at runtime."""
    hits = []

    def scan(node, where):
        for sub in ast.walk(node):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                hits.append((where, getattr(sub, "lineno", 0)))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
                if arg.annotation is not None:
                    scan(arg.annotation, node.name)
            if node.returns is not None:
                scan(node.returns, node.name)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            scan(node.annotation, "module-level annotation")
    return hits


@pytest.mark.skipif(FLOOR >= (3, 10), reason="PEP 604 is native from 3.10; the floor has moved past it")
@pytest.mark.parametrize("path", _sources(), ids=lambda p: os.path.relpath(p, ROOT))
def test_no_pep604_annotation_without_postponed_evaluation(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    if "from __future__ import annotations" in src:
        return                                  # opted in; the annotation is never evaluated
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        pytest.fail(f"{os.path.relpath(path, ROOT)} does not parse: {e}")

    hits = _pep604_in_annotations(tree)
    assert not hits, (
        f"{os.path.relpath(path, ROOT)} uses `X | Y` in an annotation Python {FLOOR[0]}.{FLOOR[1]} "
        f"evaluates at runtime, at line(s) {sorted({ln for _, ln in hits})}. Add "
        f"`from __future__ import annotations` at the top, or write Optional[...]. This imports fine on "
        f"this interpreter and raises TypeError at collection on the floor.")


def test_the_sweep_actually_covers_files():
    """A rename that emptied this list would leave a green test over nothing."""
    assert len(_sources()) > 50, len(_sources())


def test_the_floor_is_below_this_interpreter():
    """If it were not, the check above would be vacuous here and only CI would ever exercise it."""
    assert FLOOR <= sys.version_info[:2], f"declared floor {FLOOR} is newer than this interpreter"


def test_the_detector_sees_a_planted_offender():
    """The guard itself, mutated: if `_pep604_in_annotations` stopped finding anything, every file would
    pass and the sweep would read exactly like a clean result."""
    bad = ast.parse("def f(x: int | None) -> str | None:\n    return None\n")
    assert _pep604_in_annotations(bad), "the detector is blind to the exact shape that broke CI"
    good = ast.parse("from typing import Optional\ndef f(x: Optional[int]) -> Optional[str]:\n    return None\n")
    assert not _pep604_in_annotations(good), "and it must not fire on the correct spelling"


def test_a_bitwise_or_outside_an_annotation_is_not_flagged():
    """`a | b` on sets and ints is ordinary code. A guard that cried wolf on it would be turned off."""
    assert not _pep604_in_annotations(ast.parse("names = {1} | {2}\nflags = A | B\n"))
