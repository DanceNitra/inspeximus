"""Mutation testing of the evidence modules -- mutmut's operators, applied to real source files.

WHY NOT `mutmut run` DIRECTLY. mutmut 3.x rewrites every mutated file into trampolines and puts
every mutant of that file into the file. For `inspeximus/core.py` (17k lines) that file does not
import in reasonable time, and three properties of this repository turn trampolines into false
verdicts: tests read the source text back (`inspect.getsource`, docstring pins), tests drive the CLI
through `subprocess` (mutmut's per-function test map never sees a child process), and several
functions under test are decorated (mutmut skips those). So this uses mutmut's OWN mutation
operators (`mutmut.mutation.file_mutation`, mutmut 3.8.0) to enumerate mutants, and applies each
one as a plain edit to a real file in a private git worktree, the way `tools/mutation_check.py`
does. One mutant, one file on disk, one pytest process; no trampolines.

THE THREE STEPS
    python audits/2026-09-24/mutate_evidence.py generate          # -> mutants.json
    python audits/2026-09-24/mutate_evidence.py covmap            # -> covmap.json (line -> tests)
    python audits/2026-09-24/mutate_evidence.py run --workers 4   # -> results.jsonl

TEST SELECTION. `covmap` runs the candidate tests once under coverage with one context per test
(and per test's child processes, via coverage's subprocess patch), so each mutant runs only the
tests that execute its statement. A mutant that survives that set -- or that no test executes -- is
run again against every test FILE of its area (stage 2) before it is called a survivor, so a test
that only reaches the code through a subprocess the coverage map missed still gets its chance.

VERDICTS: killed (a test failed or errored), timeout (counted as killed, reported separately),
survived (stage 1 and stage 2 both green), invalid (the mutant does not compile).

Nothing here edits the checkout it runs from. Every mutant is written into a worktree under
--workdir and the worktrees are removed at the end.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------- scope
# Whole modules are mutated in full. `core.py` is mutated only inside the functions that produce or
# verify the two pieces of evidence it owns; the rest of core.py (recall, consolidation, ...) is out
# of scope for this audit.
SCOPE = {
    "erasure_certificate": [
        ("inspeximus/core.py", [
            "erasure_challenge", "sign_erasure", "verify_erasure_certificate",
            "Inspeximus.erasure_certificate", "Inspeximus._tombstone_core",
            "Inspeximus._emit_tombstone", "Inspeximus._flush_tombstones", "Inspeximus.anchor",
        ]),
    ],
    "write_receipts": [
        ("inspeximus/core.py", [
            "new_receipt_keypair", "Inspeximus._write_commit", "Inspeximus._chain_core",
            "Inspeximus._recompute_tip", "Inspeximus._receipts_disk_sig",
            "Inspeximus._reconcile_receipts_with_disk", "Inspeximus._append_receipt",
            "Inspeximus._emit_write_receipt", "Inspeximus.enable_receipts",
            "Inspeximus._persist_receipts", "Inspeximus._chain_holds_tip", "Inspeximus.verify_writes",
        ]),
    ],
    "audit_bundle": [("inspeximus/audit_bundle.py", None)],
    "action_ledger": [("inspeximus/actions.py", None)],
    "transparency_log": [("inspeximus/transparency.py", None), ("inspeximus/merkle.py", None)],
}

# Stage-2 test files per area: every test file that names the area's surface. Grepped, not curated,
# so the list cannot quietly leave out a file that exercises the code.
AREA_GREP = {
    "erasure_certificate": r"erasure_certificate|erasure-verify|erasure_challenge|sign_erasure|"
                           r"forget_subject|tombstone|\.anchor\(",
    "write_receipts": r"verify_writes|enable_receipts|receipts=True|receipt_key|receipt_signer|"
                      r"verify-writes|_emit_write_receipt|_chain_core",
    "audit_bundle": r"audit_bundle|verify_bundle|build_bundle|audit-verify|audit-bundle",
    "action_ledger": r"inspeximus\.actions|from inspeximus import actions|from inspeximus\.actions|"
                     r"ActionLedger|actions\.py",
    "transparency_log": r"inspeximus\.transparency|from inspeximus import .*transparency|"
                        r"TransparencyService|RegistrationPolicy|inspeximus\.merkle|"
                        r"from inspeximus import .*merkle|merkle\.",
}

# Test files never run against a mutant. Each one either edits source in place itself (the repo's own
# mutation harness), runs a whole-repository census whose verdict does not depend on the mutated
# behaviour, or executes long published-number probes. They were excluded before the run, not after
# looking at which mutants they would have killed.
EXCLUDE_TESTS = {
    "tests/test_mutation_check_harness.py", "tests/test_mutation_restore_is_byte_exact.py",
    "tests/test_claims_audit_mutation_score.py", "tests/test_probes_cited_by_docs.py",
    "tests/test_skip_census.py", "tests/test_perf_gate_can_fail.py",
}
# Single tests excluded because they cannot pass in the sandbox this ran in (outbound HTTPS to the
# published log is refused by the proxy). Tests that fail at baseline for any other reason are
# deselected automatically from covmap's own baseline run.
EXCLUDE_NODES = {
    "tests/test_docs_examples_are_runnable.py::test_every_documented_inspeximus_command_block_runs",
    # POLLUTERS, found by the control run (every mutant's selection run on UNMUTATED code). Both
    # replace four Inspeximus staticmethods and restore them with `getattr(Inspeximus, n)`, which
    # returns the unwrapped function, so after either one `check_self_narration` is a plain method
    # for the rest of the process and tests/test_mcp_surface.py::test_every_mcp_tool_can_be_called
    # fails with "takes 1 positional argument but 2 were given". In the full xdist suite the two
    # rarely share a worker, so it does not show; in a mutant's selection they do, and every mutant
    # would be "killed" by a test that fails without any mutation. They test audit_the_audits'
    # scoring, not the evidence code, and are reported in the audit rather than edited here.
    "tests/test_the_checks_can_actually_fail.py::test_a_pure_surface_that_always_reports_clean_is_scored_MISSED",
    "tests/test_the_checks_can_actually_fail.py::test_a_pure_surface_that_rejects_the_valid_input_is_scored_CONTROL_FAILED",
}

WORKDIR_DEFAULT = os.path.join(tempfile.gettempdir(), "inspeximus-mutation-evidence")


# ------------------------------------------------------------------------------------------ helpers
def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _function_spans(src):
    """{qualified name: (first line incl. decorators, last line)} for top-level functions and methods."""
    spans = {}
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans[node.name] = (first, node.end_lineno)
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    first = min([m.lineno] + [d.lineno for d in m.decorator_list])
                    spans[f"{node.name}.{m.name}"] = (first, m.end_lineno)
    return spans


def _function_headers(src):
    """{qualified name: (first line incl. decorators, last line before the body)}: the lines that run
    when the `def` executes -- at import, under no test's context -- not when the function is called."""
    out = {}

    def add(name, node):
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])
        out[name] = (first, max(node.lineno, node.body[0].lineno - 1))

    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{m.name}", m)
    return out


def _statement_lines(src):
    """line -> the line coverage.py records for the statement that contains it (innermost wins)."""
    out = {}

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                body = getattr(child, "body", None)
                if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
                    header_end = max(child.lineno, body[0].lineno - 1)
                    # decorators and the def line map to the def line
                    for ln in range(child.lineno, header_end + 1):
                        out[ln] = child.lineno
                else:
                    for ln in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                        out[ln] = child.lineno
            visit(child)

    visit(ast.parse(src))
    return out


def _enclosing(spans, line):
    best = None
    for name, (a, b) in spans.items():
        if a <= line <= b and (best is None or a >= spans[best][0]):
            best = name
    return best


# ----------------------------------------------------------------------------------------- generate
def generate(args):
    import libcst as cst
    from libcst.metadata import MetadataWrapper, PositionProvider
    import mutmut
    from mutmut.mutation import file_mutation as fm
    from mutmut.mutation.mutators import mutation_operators
    from mutmut.mutation.pragma_handling import get_ignored_lines

    class Visitor(fm.MutationVisitor):
        """mutmut's visitor, minus one rule: it skips decorated functions because a trampoline cannot
        wrap them. Nothing here is trampolined, so @property / @contextmanager bodies are mutated too.
        Decorator expressions themselves are still left alone."""

        def _skip_node_and_children(self, node):
            if isinstance(node, cst.FunctionDef) and node.decorators:
                return False
            return super()._skip_node_and_children(node)

    mutants = []
    seen = set()
    for area, entries in SCOPE.items():
        for rel, funcs in entries:
            path = os.path.join(ROOT, rel)
            src = _read(path)
            spans = _function_spans(src)
            if funcs is None:
                lines = None
            else:
                missing = [f for f in funcs if f not in spans]
                if missing:
                    raise SystemExit(f"{rel}: scoped function(s) not found: {missing}")
                lines = set()
                for f in funcs:
                    a, b = spans[f]
                    lines.update(range(a, b + 1))
            module = cst.parse_module(src)
            wrapper = MetadataWrapper(module)
            ignored = get_ignored_lines(rel, src, wrapper)
            visitor = Visitor(mutation_operators, ignored, lines)
            wrapper.visit(visitor)
            pos = wrapper.resolve(PositionProvider)
            line_starts = [0] + [mt.end() for mt in re.finditer("\n", src)]
            mod = wrapper.module
            n_file = 0
            for mu in visitor.mutations:
                if mu.contained_by_top_level_function is None:
                    continue                                   # module-level code: mutmut skips it too
                p = pos[mu.original_node]
                s = line_starts[p.start.line - 1] + p.start.column
                e = line_starts[p.end.line - 1] + p.end.column
                old = mod.code_for_node(mu.original_node)
                new = mod.code_for_node(mu.mutated_node)
                if src[s:e] != old:
                    # An operator node (`in`, `is not`) owns the spaces around it, and its position
                    # does not include them. Strip the same whitespace from both renderings.
                    lead = old[:len(old) - len(old.lstrip())]
                    trail = old[len(old.rstrip()):]
                    if old.strip() == src[s:e] and new.startswith(lead) and new.endswith(trail):
                        old = old.strip()
                        new = new[len(lead):len(new) - len(trail)]
                    else:
                        # Anything else: render the whole module and take the changed span.
                        full = mod.deep_replace(mu.original_node, mu.mutated_node).code
                        i = 0
                        while i < min(len(src), len(full)) and src[i] == full[i]:
                            i += 1
                        j = 0
                        while (j < min(len(src), len(full)) - i and src[len(src) - 1 - j] == full[len(full) - 1 - j]):
                            j += 1
                        s, e = i, len(src) - j
                        old, new = src[s:e], full[i:len(full) - j]
                if new == old:
                    continue
                mutated = src[:s] + new + src[e:]
                try:
                    compile(mutated, rel, "exec")
                except SyntaxError:
                    continue
                digest = hashlib.sha256((rel + "\0" + mutated).encode()).hexdigest()[:16]
                if digest in seen:
                    continue                                   # two operators, same resulting file
                seen.add(digest)
                a_line, b_line = p.start.line, p.end.line
                old_lines = src.split("\n")[a_line - 1:b_line]
                new_lines = mutated.split("\n")[a_line - 1:b_line + new.count("\n") - old.count("\n")]
                func = _enclosing(spans, a_line)
                if funcs is not None and func not in funcs:
                    continue
                mutants.append({
                    "id": f"{os.path.basename(rel)[:-3]}:{a_line}:{p.start.column}:{digest[:8]}",
                    "area": area, "file": rel, "line": a_line, "end_line": b_line,
                    "col": p.start.column, "function": func,
                    "operator": type(mu.original_node).__name__,
                    "start": s, "end": e, "replacement": new,
                    "old": old if len(old) <= 400 else old[:400] + "...",
                    "new": new if len(new) <= 400 else new[:400] + "...",
                    "old_lines": old_lines, "new_lines": new_lines,
                })
                n_file += 1
            print(f"{area:22s} {rel:32s} {n_file:6d} mutants", flush=True)
    out = args.out or os.path.join(WORKDIR_DEFAULT, "mutants.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"mutmut_version": mutmut.__version__ if hasattr(mutmut, "__version__") else "3.8.0",
                   "source_head": _git_head(), "mutants": mutants}, fh, indent=1)
    print(f"{len(mutants)} mutants -> {out}")


def _git_head():
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


# ------------------------------------------------------------------------------------------- covmap
_PLUGIN = r'''
import os, subprocess, coverage
_cov = coverage.Coverage(config_file=os.environ["MUTEV_COVRC"], data_suffix=True)
_cov.start()
import pytest

_CHILD = [None]


def _child_config(ctx):
    # coverage's subprocess patch hands children a SERIALISED config, fixed when the parent
    # started, so every child would record under the parent's empty context. Re-serialise per
    # test with the test id as the child's static context.
    cfg = _cov.config
    saved = cfg.context
    cfg.context = ctx
    try:
        _CHILD[0] = cfg.serialize()
    finally:
        cfg.context = saved
    os.environ["COVERAGE_PROCESS_CONFIG"] = _CHILD[0]


_popen_init = subprocess.Popen.__init__


def _init(self, *a, **kw):
    # Tests here often pass `env=ENV`, a dict copied from os.environ when the test module was
    # imported, so the per-test value above never reaches the child. Put it into that dict.
    env = kw.get("env")
    if env is not None and _CHILD[0] is not None:
        env = dict(env)
        env["COVERAGE_PROCESS_CONFIG"] = _CHILD[0]
        kw["env"] = env
    return _popen_init(self, *a, **kw)


subprocess.Popen.__init__ = _init


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    # Code a test MODULE runs at import (a keypair minted at module level, a fixture store built
    # while collecting) is attributed to the file, so a mutant that breaks collection selects it.
    fspath = getattr(collector, "path", None)
    is_module = fspath is not None and str(fspath).endswith(".py")
    if is_module:
        _child_config(collector.nodeid)
        _cov.switch_context(collector.nodeid)
    yield
    if is_module:
        _cov.switch_context("")
        _child_config("")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _child_config(item.nodeid)
    _cov.switch_context(item.nodeid)
    yield
    _cov.switch_context("")
    _child_config("")


def pytest_unconfigure(config):
    _cov.stop()
    _cov.save()
'''


def _area_test_files():
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    out = {}
    for area, pat in AREA_GREP.items():
        rx = re.compile(pat)
        hit = []
        for f in files:
            rel = os.path.relpath(f, ROOT).replace(os.sep, "/")
            if rel in EXCLUDE_TESTS:
                continue
            if rx.search(_read(f)):
                hit.append(rel)
        out[area] = hit
    return out


def covmap(args):
    work = args.workdir
    os.makedirs(work, exist_ok=True)
    covdir = os.path.join(work, "cov")
    shutil.rmtree(covdir, ignore_errors=True)
    os.makedirs(covdir)
    plugdir = os.path.join(work, "plugin")
    os.makedirs(plugdir, exist_ok=True)
    _write(os.path.join(plugdir, "mutev_cov.py"), _PLUGIN)
    rc = os.path.join(work, "covrc")
    _write(rc, "[run]\nsource = inspeximus\nparallel = true\n"
               f"data_file = {os.path.join(covdir, '.coverage')}\n"
               "patch = subprocess\nconcurrency = thread,multiprocessing\n")
    areas = _area_test_files()
    # THE WHOLE SUITE, not the grepped candidates: a survivor is reported as "no test executes this
    # line", and that sentence is only true if every test had the chance to execute it.
    tests = sorted(os.path.relpath(f, ROOT).replace(os.sep, "/")
                   for f in glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    tests = [t for t in tests if t not in EXCLUDE_TESTS]
    print(f"covmap over {len(tests)} test files", flush=True)
    env = dict(os.environ, MUTEV_COVRC=rc,
               PYTHONPATH=os.pathsep.join([plugdir, ROOT]))
    t0 = time.time()
    deselect = [a for n in sorted(EXCLUDE_NODES) for a in ("--deselect", n)]
    r = subprocess.run([sys.executable, "-m", "pytest", *tests, *deselect, "-p", "mutev_cov",
                        "-n", str(args.workers), "-q", "--no-header", "-p", "no:cacheprovider",
                        "--durations=0", "--tb=line"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    print(r.stdout[-1500:])
    durations = {}
    for ln in r.stdout.splitlines():
        m = re.match(r"^\s*([\d.]+)s (setup|call|teardown)\s+(\S.*)$", ln)
        if m:
            durations[m.group(3)] = durations.get(m.group(3), 0.0) + float(m.group(1))
    failed = sorted({ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith(("FAILED ", "ERROR "))})
    import coverage
    cov = coverage.Coverage(config_file=rc)
    cov.combine(data_paths=[covdir])
    data = cov.get_data()
    lines = {}
    files = {rel for entries in SCOPE.values() for rel, _ in entries}
    for rel in files:
        path = os.path.join(ROOT, rel)
        ctx = data.contexts_by_lineno(path)
        # The label is the test id itself (no static|dynamic join happens: the parent has no static
        # context and a child has no dynamic one). Do NOT split on "|": parametrised ids contain it.
        lines[rel] = {str(k): sorted({c for c in v if c}) for k, v in ctx.items()}
    out = args.out or os.path.join(args.workdir, "covmap.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"lines": lines, "durations": durations, "area_tests": areas,
                   "baseline_failed": failed, "seconds": round(time.time() - t0, 1)}, fh)
    print(f"covmap -> {out} ({time.time() - t0:.0f}s); baseline failures: {failed}")


# ---------------------------------------------------------------------------------------------- run
class Worker:
    def __init__(self, idx, work):
        self.idx = idx
        self.tree = os.path.join(work, f"wt{idx}")
        self.home = os.path.join(work, f"home{idx}")
        self.pyc = os.path.join(work, f"pyc{idx}")
        for d in (self.home, self.pyc):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        os.makedirs(os.path.join(self.home, "tmp"))

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COVERAGE_", "MUTEV_", "PYTEST_"))}
        env.update(PYTHONPATH=self.tree, HOME=self.home, USERPROFILE=self.home,
                   TMPDIR=os.path.join(self.home, "tmp"), PYTHONPYCACHEPREFIX=self.pyc,
                   PYTHONHASHSEED="0")
        return env

    def drop_pyc(self, rel):
        stem = os.path.splitext(os.path.basename(rel))[0]
        for p in glob.glob(os.path.join(self.pyc, "**", f"{stem}.*.pyc"), recursive=True):
            try:
                os.remove(p)
            except OSError:
                pass

    def pytest(self, tests, timeout):
        cmd = [sys.executable, "-m", "pytest", *tests, "-n", "0", "-x", "-q", "--no-header", "--tb=line",
               "-rfE", "-p", "no:cacheprovider", "-p", "no:randomly"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=self.tree, env=self.env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.communicate()
            return "timeout", None, time.time() - t0, ""
        killers = [ln.split()[1] for ln in out.splitlines() if ln.startswith(("FAILED ", "ERROR "))]
        rc = proc.returncode
        if rc == 0:
            return "passed", None, time.time() - t0, ""
        if rc == 5:
            return "nocollect", None, time.time() - t0, out[-800:]
        if rc in (1, 2) or killers:
            return "failed", (killers[0] if killers else f"rc={rc}"), time.time() - t0, out[-600:]
        return "error", f"rc={rc}", time.time() - t0, out[-800:]

    def reset(self):
        subprocess.run(["git", "checkout", "-q", "--", "."], cwd=self.tree)
        subprocess.run(["git", "clean", "-fdq"], cwd=self.tree)


_GROUP_SUFFIX = re.compile(r"@[A-Za-z0-9_.-]+$")


def _norm_nodeid(t):
    return _GROUP_SUFFIX.sub("", t) if "::" in t else t


class Selector:
    """Which tests a mutant runs, read from covmap.

    stage 1: the tests that execute the mutated statement (its coverage context). A test that does
             not execute the statement runs exactly the code it ran before, with two exceptions,
             and stage 2 covers each of them:
    stage 2: (a) a mutant on a `def` line (a default argument, evaluated at import under no test's
             context) or on a statement no test executes: every test that executes ANY line of the
             enclosing function -- mutmut's own granularity;
             (b) otherwise, the rest of every test FILE that holds a stage-1 test and declares a
             module-, class-, package- or session-scoped fixture: there one test's context builds a
             fixture another test's assertions read.
    A context without '::' is a test FILE that ran the code while being imported; it selects the
    whole file.
    """

    def __init__(self, cm):
        # `--dist loadgroup` (pytest.ini) makes xdist append "@<group>" to the id of every test in
        # an xdist_group, and that id does not exist in the serial run a mutant gets. Strip it.
        norm = _norm_nodeid
        cm = dict(cm)
        cm["lines"] = {rel: {ln: sorted({norm(t) for t in ts}) for ln, ts in m.items()}
                       for rel, m in cm["lines"].items()}
        durations = {}
        for k, v in cm["durations"].items():
            durations[norm(k)] = durations.get(norm(k), 0.0) + v
        cm["durations"] = durations
        self.cm = cm
        self.bad = {norm(t) for t in cm.get("baseline_failed", [])} | EXCLUDE_NODES
        self.file_dur = {}
        for k, v in cm["durations"].items():
            f = k.split("::")[0]
            self.file_dur[f] = self.file_dur.get(f, 0.0) + v
        self.stmt, self.spans, self.heads, self.fn_cache = {}, {}, {}, {}
        self._lock = threading.Lock()          # four worker threads share one selector
        wide = re.compile(r"scope\s*=\s*[\"'](module|class|package|session)[\"']")
        self.wide_files = {os.path.relpath(f, ROOT).replace(os.sep, "/")
                           for f in glob.glob(os.path.join(ROOT, "tests", "test_*.py"))
                           if wide.search(_read(f))} - EXCLUDE_TESTS

    def _ok(self, t):
        return bool(t) and t.split("::")[0] not in EXCLUDE_TESTS and t not in self.bad

    def _lines(self, rel, lines):
        ctx = self.cm["lines"].get(rel, {})
        out = set()
        for ln in lines:
            out.update(ctx.get(str(ln), []))
        return {t for t in out if self._ok(t)}

    def _prep(self, rel):
        with self._lock:
            if rel not in self.spans:
                src = _read(os.path.join(ROOT, rel))
                self.stmt[rel] = _statement_lines(src)
                self.heads[rel] = _function_headers(src)
                self.spans[rel] = _function_spans(src)

    def stage1(self, m):
        rel = m["file"]
        self._prep(rel)
        lines = set()
        for ln in range(m["line"], m["end_line"] + 1):
            lines.add(ln)
            lines.add(self.stmt[rel].get(ln, ln))
        return self._lines(rel, lines)

    def stage2(self, m, t1):
        rel = m["file"]
        self._prep(rel)
        a, b = self.heads[rel][m["function"]]
        header = a <= m["line"] <= b
        if not t1 and not header and not self.sampled(m):
            return set()
        if not t1 or header:
            key = (rel, m["function"])
            with self._lock:
                if key not in self.fn_cache:
                    fa, fb = self.spans[rel][m["function"]]
                    self.fn_cache[key] = self._lines(rel, range(fa, fb + 1))
                return self.fn_cache[key] - t1
        return {t.split("::")[0] for t in t1 if t.split("::")[0] in self.wide_files}

    @staticmethod
    def sampled(m):
        """1 in 10 of the mutants on a statement no test executes still runs stage 2 (a).

        Measured on the first 909 mutants of this run: 44 sat on an unexecuted statement, stage 2
        ran every test of the enclosing function against each (3-4 minutes apiece in core.py), and
        not one was killed there -- the coverage map had not missed a test. The sample keeps checking
        that for the rest of the run at a tenth of the cost; a kill in it is reported."""
        return int(hashlib.sha256(m["id"].encode()).hexdigest(), 16) % 10 == 0

    def args(self, tests):
        """Order cheapest first (so -x stops early on a kill) and fold node ids into their file
        when the whole file is selected anyway."""
        files = {t for t in tests if "::" not in t}
        nodes = {t for t in tests if "::" in t and t.split("::")[0] not in files}
        dur = self.cm["durations"]
        deselect = [a for n in sorted(self.bad) if n.split("::")[0] in files for a in ("--deselect", n)]
        return (sorted(nodes, key=lambda t: (dur.get(t, 0.005), t))
                + sorted(files, key=lambda f: (self.file_dur.get(f, 5.0), f)) + deselect)

    def budget(self, tests):
        dur = self.cm["durations"]
        # pytest hides durations under 5 ms, so a test missing from the map is one of those
        est = sum(self.file_dur.get(t, 5.0) if "::" not in t else dur.get(t, 0.005) for t in tests)
        return min(1500, 90 + 4 * est)


def run(args):
    with open(args.mutants, encoding="utf-8") as fh:
        spec = json.load(fh)
    with open(args.covmap, encoding="utf-8") as fh:
        cm = json.load(fh)
    mutants = spec["mutants"]
    if args.only:
        rx = re.compile(args.only)
        mutants = [m for m in mutants if rx.search(m["id"]) or rx.search(m["area"])]
    if args.sample:
        import random
        rng = random.Random(20260924)
        by_fn = {}
        for m in mutants:
            by_fn.setdefault((m["file"], m["function"]), []).append(m)
        # one mutant per function first, so the sample reaches every selection shape
        picked = [rng.choice(v) for v in by_fn.values()]
        rest = [m for m in mutants if m not in picked]
        mutants = (picked + rng.sample(rest, max(0, args.sample - len(picked))))[:max(args.sample, len(picked))]
    done = {}
    if os.path.exists(args.out) and not args.fresh:
        for ln in open(args.out, encoding="utf-8"):
            if ln.strip():
                r = json.loads(ln)
                done[r["id"]] = r
    # The small evidence surfaces first, the 7k-mutant action ledger last, so partial results are
    # already complete per area if a run is interrupted.
    order = {a: i for i, a in enumerate(("transparency_log", "erasure_certificate", "write_receipts",
                                         "audit_bundle", "action_ledger"))}
    todo = sorted((m for m in mutants if m["id"] not in done),
                  key=lambda m: (order.get(m["area"], 9), m["file"], m["line"], m["col"]))
    print(f"{len(mutants)} mutants, {len(done)} already done, {len(todo)} to run, {args.workers} workers",
          flush=True)
    work = args.workdir
    os.makedirs(work, exist_ok=True)
    workers = []
    for i in range(args.workers):
        w = Worker(i, work)
        if os.path.exists(w.tree):
            subprocess.run(["git", "worktree", "remove", "--force", w.tree], cwd=ROOT, capture_output=True)
            shutil.rmtree(w.tree, ignore_errors=True)
        # The worktree is the commit the mutants were generated from, so the suite being measured is
        # the one that existed then -- tests written from this run's survivors are not in it.
        rev = spec.get("source_head") or "HEAD"
        r = subprocess.run(["git", "worktree", "add", "--detach", "-f", w.tree, rev], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr)
        # the worktree must hold the SAME library source this run's mutants were generated from
        for rel in {m["file"] for m in mutants}:
            if _read(os.path.join(w.tree, rel)) != _read(os.path.join(ROOT, rel)):
                raise SystemExit(f"{rel}: worktree differs from the checkout; commit or stash first")
        workers.append(w)

    sel = Selector(cm)
    src_cache = {}
    q = queue.Queue()
    for m in todo:
        q.put(m)
    lock = threading.Lock()
    out_fh = open(args.out, "w" if args.fresh else "a", encoding="utf-8")
    counts = {}
    t_start = time.time()
    n_done = [0]

    def one(w, m):
        t1 = sel.stage1(m)
        t2 = sel.stage2(m, t1)
        path = os.path.join(w.tree, m["file"])
        with lock:
            if m["file"] not in src_cache:
                src_cache[m["file"]] = _read(os.path.join(ROOT, m["file"]))
            orig = src_cache[m["file"]]
        mutated = orig[:m["start"]] + m["replacement"] + orig[m["end"]:]
        if args.control:
            mutated = orig                 # the selection alone, on unmutated code: must all pass
        res = {"id": m["id"], "area": m["area"], "file": m["file"], "line": m["line"],
               "function": m["function"], "n_stage1": len(t1), "n_stage2": len(t2)}
        w.drop_pyc(m["file"])
        _write(path, mutated)
        try:
            for stage, tests in ((1, t1), (2, t2)):
                if not tests:
                    res[f"stage{stage}"] = "none_selected"
                    continue
                status, killer, secs, tail = w.pytest(sel.args(tests), sel.budget(tests))
                res.update({f"stage{stage}": status, f"stage{stage}_s": round(secs, 1)})
                if status in ("failed", "timeout"):
                    res.update(verdict="killed" if status == "failed" else "timeout", killer=killer,
                               stage=stage)
                    return res
                if status != "passed":
                    res.update(verdict="error", detail=tail)
                    return res
            if not t1 and not t2 and "function" in m:
                res["stage2"] = "unexecuted_not_sampled"
            res["verdict"] = "survived"
            return res
        finally:
            _write(path, orig)
            w.drop_pyc(m["file"])
            w.reset()

    def loop(w):
        while True:
            try:
                m = q.get_nowait()
            except queue.Empty:
                return
            try:
                res = one(w, m)
            except Exception as e:                              # noqa: BLE001
                res = {"id": m["id"], "area": m["area"], "file": m["file"], "line": m["line"],
                       "verdict": "error", "detail": repr(e)[:300]}
            with lock:
                out_fh.write(json.dumps(res) + "\n")
                out_fh.flush()
                counts[res["verdict"]] = counts.get(res["verdict"], 0) + 1
                n_done[0] += 1
                if n_done[0] % 25 == 0:
                    el = time.time() - t_start
                    rate = n_done[0] / el * 60
                    print(f"[{n_done[0]}/{len(todo)}] {rate:.1f}/min {counts}", flush=True)

    with cf.ThreadPoolExecutor(len(workers)) as ex:
        list(ex.map(loop, workers))
    out_fh.close()
    if not args.keep:
        for w in workers:
            subprocess.run(["git", "worktree", "remove", "--force", w.tree], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
    print(f"done in {time.time() - t_start:.0f}s: {counts}")


# ------------------------------------------------------------------------------------------- report
_MSG = re.compile(r"problems\.append|problems \+=|\bbad\(|why\.append|\braise\b|limits\.append|"
                  r"notes?\.append|print\(|warnings\.warn|\bwarn\(|_why\b|RuntimeError|ValueError|"
                  r"TypeError|problems\.extend")


def _statement_text(src_lines, stmt_map, line):
    first = stmt_map.get(line, line)
    last = line
    while last + 1 <= len(src_lines) and stmt_map.get(last + 1) == first:
        last += 1
    return "\n".join(src_lines[first - 1:last])


def classify(m, r, src_lines, stmt_map):
    """A first-pass reason for a survivor, from the shape of the mutation and where it sits.
    notes.json overrides it per mutant with a reason written after reading the code and the tests."""
    fn = m["function"]
    if r.get("verdict") == "no_coverage":
        return "unexecuted", f"no test in the suite executes `{fn}` at all"
    if r.get("stage1") == "none_selected":
        return "unexecuted", (f"no test executes line {m['line']}; `{fn}` runs under "
                              f"{r.get('n_stage2', 0)} test(s), none of which reaches this statement")
    stmt = _statement_text(src_lines, stmt_map, m["line"])
    op = m["operator"]
    old, new = m["old"], m["new"]
    if op in ("SimpleString", "ConcatenatedString"):
        if _MSG.search(stmt):
            return "message", "wording of a human-readable message; the tests assert that it is reported, not what it says"
        lit = old.strip("\"'")
        if re.search(re.escape(old) + r"\s*:", stmt):
            return "unread_field", f"output key `{lit}` is written but no test reads it back"
        if re.search(r"\.get\(\s*" + re.escape(old), stmt) or re.search(r"\[\s*" + re.escape(old) + r"\s*\]", stmt):
            return "unread_field", f"lookup of `{lit}`: no test input makes the renamed key read differently"
        return "literal", f"literal `{old[:40]}` is not asserted by any test"
    if op in ("Integer", "Float"):
        return "constant", f"constant {old} -> {new}: no test sits on the boundary this constant draws"
    if op in ("ComparisonTarget", "BooleanOperation", "UnaryOperation", "IsNot", "Is", "In", "NotIn",
              "IfExp", "Name"):
        return "condition", "condition changed and no test input makes the original and the mutant disagree"
    if op == "Call":
        return "call_arg", "a call argument dropped or set to None, and no test observes the difference"
    if op in ("Assign", "AnnAssign", "AugAssign"):
        return "assignment", "assigned value replaced and no test observes it"
    if op == "BinaryOperation":
        return "arithmetic", "arithmetic operator swapped and no test observes the result"
    return "other", f"{op} mutation not observed by any test"


def report(args):
    with open(args.mutants, encoding="utf-8") as fh:
        spec = json.load(fh)
    results = {}
    for ln in open(args.results, encoding="utf-8"):
        if ln.strip():
            r = json.loads(ln)
            results[r["id"]] = r
    notes = {}
    if args.notes and os.path.exists(args.notes):
        with open(args.notes, encoding="utf-8") as fh:
            notes = json.load(fh)
    srcs, stmts = {}, {}
    survivors, totals = [], {}
    for m in spec["mutants"]:
        r = results.get(m["id"])
        t = totals.setdefault(m["area"], {"mutants": 0, "run": 0, "killed": 0, "timeout": 0,
                                          "survived": 0, "no_coverage": 0, "error": 0})
        t["mutants"] += 1
        if r is None:
            continue
        t["run"] += 1
        t[r["verdict"]] = t.get(r["verdict"], 0) + 1
        if r["verdict"] not in ("survived", "no_coverage"):
            continue
        if m["file"] not in srcs:
            srcs[m["file"]] = _read(os.path.join(ROOT, m["file"])).split("\n")
            stmts[m["file"]] = _statement_lines(_read(os.path.join(ROOT, m["file"])))
        cat, why = classify(m, r, srcs[m["file"]], stmts[m["file"]])
        note = notes.get(m["id"]) or notes.get(f"{m['file']}:{m['line']}")
        if isinstance(note, dict):
            cat, why = note.get("category", cat), note.get("why", why)
        elif isinstance(note, str):
            why = note
        survivors.append({"id": m["id"], "area": m["area"], "file": m["file"], "line": m["line"],
                          "function": m["function"], "operator": m["operator"],
                          "old": m["old_lines"], "new": m["new_lines"],
                          "node_old": m["old"], "node_new": m["new"],
                          "category": cat, "why": why, "verdict": r["verdict"],
                          "n_stage1": r.get("n_stage1", 0), "n_stage2": r.get("n_stage2", 0)})
    kills = {}
    for path in (args.kills or []):
        with open(path, encoding="utf-8") as fh:
            for i, v in json.load(fh).items():
                if v.get("verdict") == "KILLED":
                    kills[i] = v.get("killer")
    for sv in survivors:
        sv["killed_now_by"] = kills.get(sv["id"])
    for t in totals.values():
        det = t["killed"] + t["timeout"]
        denom = det + t["survived"] + t["no_coverage"]
        t["score"] = round(det / denom, 4) if denom else None
    for a, t in totals.items():
        left = sum(1 for sv in survivors if sv["area"] == a and not sv["killed_now_by"])
        denom = t["killed"] + t["timeout"] + t["survived"] + t["no_coverage"]
        t["survived_now"] = left
        t["score_now"] = round((denom - left) / denom, 4) if denom else None
    out = {"source_head": spec.get("source_head"), "totals": totals, "survivors": survivors}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(_survivor_tables(out))
    print(json.dumps(totals, indent=1))
    print(f"{len(survivors)} survivors -> {args.out}")


AREA_TITLES = {
    "erasure_certificate": "Erasure certificate",
    "write_receipts": "Write receipts",
    "audit_bundle": "Audit bundle",
    "action_ledger": "Action ledger",
    "transparency_log": "Transparency log",
}


def _cell(text, limit=90):
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit - 1] + "\u2026"
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}".replace("|", "\\|")


def _survivor_tables(out):
    """Every survivor, one row each, grouped by area, then file and function, in line order."""
    lines = []
    for area, title in AREA_TITLES.items():
        rows = [sv for sv in out["survivors"] if sv["area"] == area]
        lines.append(f"### {title}: {len(rows)} survivors\n")
        by_fn = {}
        for sv in sorted(rows, key=lambda r: (r["file"], r["line"], r["id"])):
            by_fn.setdefault((sv["file"], sv["function"]), []).append(sv)
        for (rel, fn), svs in by_fn.items():
            lines.append(f"**`{rel}` `{fn}`** ({len(svs)})\n")
            lines.append("| line | mutation | why no test caught it | now |")
            lines.append("|---:|---|---|---|")
            for sv in svs:
                old = " ".join(sv["old"]) if isinstance(sv["old"], list) else sv["old"]
                m_old, m_new = sv.get("node_old", ""), sv.get("node_new", "")
                mut = f"{_cell(m_old, 60)} \u2192 {_cell(m_new, 60)}" if (m_old or m_new) else _cell(old)
                why = sv["why"].replace("|", "\\|")
                now = ("killed by `" + sv["killed_now_by"].split("::")[-1] + "`") if sv["killed_now_by"] else ""
                lines.append(f"| {sv['line']} | {mut} | [{sv['category']}] {why} | {now} |")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- kill
def kill(args):
    """Prove a new test has teeth: it must PASS on the original source and FAIL on each mutant named.

    Runs in a private worktree of HEAD with the working tree's copy of each test file laid over it,
    so the check sees the new tests without the checkout's library code ever being edited."""
    with open(args.mutants, encoding="utf-8") as fh:
        by_id = {m["id"]: m for m in json.load(fh)["mutants"]}
    if args.ids_file:
        args.ids = [ln.strip() for ln in open(args.ids_file, encoding="utf-8") if ln.strip()]
    work = args.workdir
    w = Worker(f"k{os.getpid()}", work)        # one worktree per invocation: two checks may run at once
    if os.path.exists(w.tree):
        subprocess.run(["git", "worktree", "remove", "--force", w.tree], cwd=ROOT, capture_output=True)
        shutil.rmtree(w.tree, ignore_errors=True)
    subprocess.run(["git", "worktree", "add", "--detach", "-f", w.tree, "HEAD"], cwd=ROOT,
                   capture_output=True, check=True)
    ok = True
    verdicts = {}
    try:
        for t in args.tests:
            f = t.split("::")[0]
            shutil.copyfile(os.path.join(ROOT, f), os.path.join(w.tree, f))
        for rel in {by_id[i]["file"] for i in args.ids}:
            if _read(os.path.join(w.tree, rel)) != _read(os.path.join(ROOT, rel)):
                raise SystemExit(f"{rel}: the checkout's library source differs from HEAD")
        status, killer, secs, tail = w.pytest(args.tests, 600)
        print(f"original : {status} ({secs:.1f}s)")
        if status != "passed":
            print(tail)
            ok = False
        for i in args.ids:
            m = by_id[i]
            path = os.path.join(w.tree, m["file"])
            orig = _read(path)
            _write(path, orig[:m["start"]] + m["replacement"] + orig[m["end"]:])
            w.drop_pyc(m["file"])
            try:
                status, killer, secs, tail = w.pytest(args.tests, 600)
            finally:
                _write(path, orig)
                w.drop_pyc(m["file"])
            verdict = "KILLED" if status in ("failed", "timeout") else "SURVIVES"
            print(f"{i:40s}: {verdict} ({status}, {killer})", flush=True)
            verdicts[i] = {"verdict": verdict, "status": status, "killer": killer}
            ok = ok and verdict == "KILLED"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", w.tree], cwd=ROOT, capture_output=True)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(verdicts, fh, indent=1)
    print("ALL KILLED, original green" if ok else "NOT ALL KILLED")
    sys.exit(0 if ok else 1)


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out")
    c = sub.add_parser("covmap")
    c.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    c.add_argument("--workdir", default=WORKDIR_DEFAULT)
    c.add_argument("--out")
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    r.add_argument("--workdir", default=WORKDIR_DEFAULT)
    r.add_argument("--mutants", default=os.path.join(WORKDIR_DEFAULT, "mutants.json"))
    r.add_argument("--covmap", default=os.path.join(WORKDIR_DEFAULT, "covmap.json"))
    r.add_argument("--out", default=os.path.join(WORKDIR_DEFAULT, "results.jsonl"))
    r.add_argument("--only", help="regex over mutant id or area")
    r.add_argument("--fresh", action="store_true", help="ignore results already in --out")
    r.add_argument("--keep", action="store_true", help="leave the worktrees in place")
    r.add_argument("--control", action="store_true",
                   help="run each mutant's test selection WITHOUT applying it; every one must pass")
    r.add_argument("--sample", type=int, default=0, help="run a random sample of this many mutants")
    p = sub.add_parser("report")
    p.add_argument("--mutants", default=os.path.join(WORKDIR_DEFAULT, "mutants.json"))
    p.add_argument("--results", default=os.path.join(WORKDIR_DEFAULT, "results.jsonl"))
    p.add_argument("--notes", default=os.path.join(HERE, "notes.json"))
    p.add_argument("--out", default=os.path.join(HERE, "survivors.json"))
    p.add_argument("--kills", nargs="*", help="JSON verdicts from `kill --json-out` runs of the new tests")
    p.add_argument("--md-out", help="write the per-module survivor tables here (markdown)")
    k = sub.add_parser("kill")
    k.add_argument("--mutants", default=os.path.join(WORKDIR_DEFAULT, "mutants.json"))
    k.add_argument("--workdir", default=WORKDIR_DEFAULT)
    k.add_argument("--ids", nargs="+", default=[])
    k.add_argument("--tests", nargs="+", required=True)
    k.add_argument("--ids-file", help="read mutant ids from this file (one per line) instead of --ids")
    k.add_argument("--json-out", help="write {id: verdict} here")
    a = ap.parse_args(argv)
    {"generate": generate, "covmap": covmap, "run": run, "report": report, "kill": kill}[a.cmd](a)


if __name__ == "__main__":
    main()
