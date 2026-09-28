"""Tools that create stores of their own keep their chain heads out of the real key home.

MEASURED 2026-09-28 on PC1: the real %APPDATA%\\inspeximus\\heads held 2,910 heads, 2,589 of them for stores
that no longer exist, 1,184 of those from governance_audit.py alone. A concurrent release_check wrote 13 in
one suite run and failed that run's real-home guard. Every tool that runs outside pytest and creates stores
now calls tools/_key_home.py's `isolate` first. These tests hold the wiring (one line per script, so one
mutant per script), the helper's behaviour, and one real tool end to end, each with a control that shows
the leak when isolation is off.
"""
import ast
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Entry points that create stores and do NOT act on a real one. The table in B_findings lists the rest
#: with the reason each is left alone (a real store, a real signing key, no store at all, or already
#: sandboxed).
ISOLATED = ["claims_audit.py", "governance_audit.py", "store_audit.py", "adk_audit.py", "haystack_audit.py",
            "checkpointer_conformance.py", "tools/release_check.py", "tools/mutation_check.py",
            "tools/mutation_check_parallel.py", "tools/gen_coverage_table.py", "tools/gen_robustness_evidence.py",
            "tools/page_runs.py", "tools/time_to_demo.py", "tools/integration_conformance.py",
            "tools/readme_blocks.py", "tools/suite_parallel.py"]


def _isolates_first(rel: str) -> bool:
    """The script's entry code calls `_key_home.isolate` before anything else it runs."""
    tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())

    def is_isolate(stmt):
        return "_key_home" in ast.unparse(stmt) and ".isolate(" in ast.unparse(stmt)
    for node in tree.body:
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            body = [s for s in node.body if not (isinstance(s, ast.Import) or
                                                 (isinstance(s, ast.Expr) and "sys.path" in ast.unparse(s)))]
            return bool(body) and is_isolate(body[0])
    # A script with no __main__ block runs its work at import: the call must come before the first
    # statement that is not an import, a docstring, a sys.path line or a definition.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Expr) and (isinstance(node.value, ast.Constant) or "sys.path" in ast.unparse(node)):
            continue
        return is_isolate(node)
    return False


@pytest.mark.parametrize("rel", ISOLATED)
def test_each_tool_isolates_its_key_home_before_it_runs(rel):
    assert _isolates_first(rel), f"{rel} runs without isolating INSPEXIMUS_KEY_HOME first"


def test_the_wiring_check_can_fail(tmp_path):
    """The control for the check above: a script whose entry block does work before isolating is refused."""
    bad = tmp_path / "bad.py"
    bad.write_text('if __name__ == "__main__":\n    main()\n    __import__("_key_home").isolate("x")\n',
                   encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text('if __name__ == "__main__":\n    __import__("_key_home").isolate("x")\n    main()\n',
                    encoding="utf-8")
    rel_bad, rel_good = os.path.relpath(bad, ROOT), os.path.relpath(good, ROOT)
    assert not _isolates_first(rel_bad) and _isolates_first(rel_good)


def _env(sandbox, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("INSPEXIMUS_KEY_HOME", "_INSPEXIMUS_TOOL_KEY_HOME", "INSPEXIMUS_TOOL_REAL_KEY_HOME")}
    env.update(APPDATA=str(sandbox), PYTHONPATH=ROOT + os.pathsep + env.get("PYTHONPATH", ""), **extra)
    return env


def _heads(sandbox):
    d = os.path.join(str(sandbox), "inspeximus", "heads")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


CHILD = r'''
import os, sys, tempfile
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
d = __import__("_key_home").isolate("selftest")
from inspeximus import Inspeximus
m = Inspeximus(os.path.join(tempfile.mkdtemp(), "store.json"), receipts=True, receipt_key="11" * 32)
m.remember("a fact"); m.flush()
print("KEYHOME", d)
print("HEADS_IN_KEYHOME", len(os.listdir(os.path.join(d, "inspeximus", "heads"))) if d and os.path.isdir(os.path.join(d, "inspeximus", "heads")) else 0)
'''


@pytest.mark.parametrize("real", [False, True], ids=["isolated", "control_real_key_home"])
def test_a_tool_process_writes_no_head_into_the_real_key_home(tmp_path, real):
    sandbox = tmp_path / "appdata"
    sandbox.mkdir()
    extra = {"INSPEXIMUS_TOOL_REAL_KEY_HOME": "1"} if real else {}
    r = subprocess.run([sys.executable, "-c", CHILD, ROOT], env=_env(sandbox, **extra), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    if real:
        assert _heads(sandbox), "control: with the real key home kept, the head lands in APPDATA"
        return
    assert _heads(sandbox) == [], "a head reached the (sandboxed) real key home"
    keyhome = [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("KEYHOME")][0]
    assert not os.path.exists(keyhome), "the isolated key home was not removed at exit"


def test_a_nested_tool_reuses_its_parents_key_home(tmp_path):
    sandbox = tmp_path / "appdata"
    sandbox.mkdir()
    code = ('import os, sys, subprocess; sys.path.insert(0, os.path.join(sys.argv[1], "tools")); '
            'd = __import__("_key_home").isolate("parent"); '
            'c = subprocess.run([sys.executable, "-c", "import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], '
            '\'tools\')); print(__import__(\'_key_home\').isolate(\'child\'))", sys.argv[1]], capture_output=True, text=True); '
            'print(d == c.stdout.strip())')
    r = subprocess.run([sys.executable, "-c", code, ROOT], env=_env(sandbox), capture_output=True, text=True,
                       timeout=120)
    assert r.stdout.strip().endswith("True"), r.stdout + r.stderr


@pytest.mark.parametrize("real", [False, True], ids=["isolated", "control_real_key_home"])
def test_governance_audit_end_to_end_leaves_the_real_heads_alone(tmp_path, real):
    """The worst writer measured on PC1 (1,184 orphaned heads), run for real with a sandboxed APPDATA."""
    sandbox = tmp_path / "appdata"
    sandbox.mkdir()
    extra = {"INSPEXIMUS_TOOL_REAL_KEY_HOME": "1"} if real else {}
    r = subprocess.run([sys.executable, "governance_audit.py", "--local", "--repeats", "1"], cwd=ROOT,
                       env=_env(sandbox, **extra), capture_output=True, text=True, timeout=900)
    if real:
        assert _heads(sandbox), f"control: the audit wrote no head at all, so the test sees nothing: " \
                                f"{r.stdout[-400:]}{r.stderr[-400:]}"
        return
    assert _heads(sandbox) == [], f"governance_audit.py left {len(_heads(sandbox))} head(s) in the real key home"
