"""Run every fenced ```python block in README.md, and check every result a block states in a comment.

The README's Python blocks are the first code most readers run, and a comment such as
`# 'The staging database is db-7.internal'` is a promise about what they will see. The onboarding review
of 2026-09-24 (audits/2026-09-24/onboarding-review.md on the review-onboarding branch) ran each block
cold and found two ways they break: run one after another in a single folder, three blocks return
something other than what their comments promise; and the signing block needs `cryptography`, which a
plain `pip install inspeximus` does not bring. This runs the blocks both ways so either defect fails a build.

HOW A BLOCK RUNS. Each block runs in a fresh interpreter (a subprocess), with its working directory and
HOME set to directories this script chooses, so a store file or a receipt head written by one run is
seen only by runs that share that directory. The block's top-level statements execute in order, in one
namespace, exactly as `python block.py` would run them. Tracebacks carry README.md line numbers.

WHAT COUNTS AS A STATED RESULT. For a top-level expression statement:
  * a trailing comment that reads as a Python literal:   m.verify()      # (True, [])
  * or, failing that, the comment line directly below:    m.current("k")
                                                          # None
The literal is compared with `==` to the value of the expression. Text after the literal is allowed
(`# 'db-7'   <- the correction wins`). For `print(...)`, the comment line directly below states the
printed text. A comment that starts like a literal but does not parse is an error, not a silent pass;
a comment that starts with a word (`# a correction`) is prose and is not checked.

EXTRAS. A block needs `inspeximus[crypto]` (or any other extra) when that exact text appears in the
README between the previous Python block and this one. Where the interpreter under test lacks a
declared extra, the block is expected to FAIL and to name the missing module, so a stale declaration is
caught as well as a missing one.

    python tools/readme_blocks.py                        # this checkout, via PYTHONPATH
    python tools/readme_blocks.py --python venv/bin/python --layout shared
    python tools/readme_blocks.py --exec BLOCK.py --first-line N     (internal: run one block)
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

#: The module each extra brings, for extras a README block or an example may declare. A declared extra
#: missing here is an error in the caller, so a new declaration cannot be silently ignored.
EXTRA_MODULE = {"crypto": "cryptography", "langgraph": "langgraph"}

_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
_EXTRA = re.compile(r"inspeximus\[([A-Za-z0-9_,-]+)\]")
#: A comment that starts like a Python literal. Anything else is prose and is not checked.
_LITERAL_START = re.compile(r"""^(['"\[({]|-?\d|True\b|False\b|None\b)""")


class Block:
    """One ```python fence: `line` is the README line of the opening fence (the L-number reviews use)."""

    def __init__(self, line: int, code: str, extras: tuple):
        self.line = line
        self.code = code
        self.extras = extras

    @property
    def name(self) -> str:
        return "README.md:L%d" % self.line


def extras_declared(text: str) -> tuple:
    """Every `inspeximus[extra]` named in `text`, in order, without duplicates."""
    out = []
    for m in _EXTRA.finditer(text):
        for extra in m.group(1).split(","):
            extra = extra.strip()
            if extra and extra not in out:
                out.append(extra)
    return tuple(out)


def blocks(path: str = README) -> list:
    """Every ```python block in the file, in order, with the extras declared since the previous one."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out, i, since = [], 0, []
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            since.append(lines[i])
            i += 1
            continue
        lang, start = m.group(1).lower(), i
        i += 1
        body = []
        while i < len(lines) and not lines[i].startswith("```"):
            body.append(lines[i])
            i += 1
        i += 1                                     # the closing fence
        if lang == "python":
            out.append(Block(start + 1, "\n".join(body) + "\n", extras_declared("\n".join(since))))
            since = []
        else:
            since.extend(body)                     # a ```bash `pip install "inspeximus[crypto]"` declares too
    return out


# ── stated results ──────────────────────────────────────────────────────────────────────────────────
class Unreadable(ValueError):
    """A comment that starts like a stated result and is not one Python can read."""


def _literal(comment: str):
    """(True, value) when the comment states a literal, (False, None) when it is prose.

    The longest prefix that parses wins, so `'db-7'   <- the correction wins` reads as 'db-7'."""
    text = comment.strip()
    if not _LITERAL_START.match(text):
        return False, None
    for end in range(len(text), 0, -1):
        if end < len(text) and not text[end].isspace():
            continue
        try:
            return True, ast.literal_eval(text[:end].strip())
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    raise Unreadable("the comment %r starts like a stated result but is not a Python literal" % comment)


def _comments(code: str):
    """{line: comment text}, and the set of lines that hold only a comment."""
    comments, code_lines = {}, set()
    for tok in tokenize.generate_tokens(io.StringIO(code).readline):
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string[1:]
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                              tokenize.ENDMARKER, tokenize.ENCODING):
            for ln in range(tok.start[0], tok.end[0] + 1):
                code_lines.add(ln)
    only = {ln for ln in comments if ln not in code_lines}
    return comments, only


def stated_results(code: str) -> list:
    """[(index, kind, expected, line)] for every top-level statement that states a result.

    `index` is the statement's position in the block's top-level body. kind is "value" (compare the
    expression's value) or "printed" (compare what print() wrote). `line` is the line of the comment,
    relative to the block."""
    tree = ast.parse(code)
    comments, only = _comments(code)
    out = []
    for index, stmt in enumerate(tree.body):
        if not isinstance(stmt, ast.Expr):
            continue
        end = stmt.end_lineno
        below = comments.get(end + 1) if (end + 1) in only else None
        call = stmt.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"):
            if below is not None:
                out.append((index, "printed", below.strip(), end + 1))
            continue
        trailing = comments.get(end) if end not in only else None
        if trailing is not None:
            ok, value = _literal(trailing)
            if ok:
                out.append((index, "value", value, end))
                continue
        if below is not None:
            ok, value = _literal(below)
            if ok:
                out.append((index, "value", value, end + 1))
    return out


# ── running one block (inside the interpreter under test) ───────────────────────────────────────────
def _exec_block(path: str, first_line: int) -> int:
    """Run the block in `path`, whose first line is README line `first_line`.

    Exit 0 when every statement ran and every stated result held. Otherwise exit 1, with one stderr line
    `FAILED README.md:L<n>: <what happened>` per problem. A wrong result is reported and the block goes
    on, so one run names every mismatch; an exception stops it, as it would stop a reader's script."""
    # This file's own directory must not shadow anything the block imports; a script run by a reader
    # has its own folder first, and that is the working directory here.
    sys.path[0] = os.getcwd()
    with open(path, encoding="utf-8") as fh:
        code = fh.read()
    offset = first_line - 1
    try:
        checks = {index: (kind, expected, line) for index, kind, expected, line in stated_results(code)}
    except Unreadable as e:
        sys.stderr.write("FAILED README.md:L%d: %s\n" % (first_line, e))
        return 1
    tree = ast.parse(code)
    ns = {"__name__": "__main__", "__file__": os.path.join(os.getcwd(), "block.py")}
    held, wrong = 0, 0
    for index, stmt in enumerate(tree.body):
        check = checks.get(index)
        ast.increment_lineno(stmt, offset)
        try:
            if check and check[0] == "value":
                got = eval(compile(ast.Expression(stmt.value), "README.md", "eval", dont_inherit=True), ns)
                problem = (None if got == check[1] else
                           "the block states %r; it returned %r" % (check[1], got))
            elif check and check[0] == "printed":
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    exec(compile(ast.Module([stmt], type_ignores=[]), "README.md", "exec", dont_inherit=True), ns)
                sys.stdout.write(buf.getvalue())
                printed = buf.getvalue().strip()
                problem = (None if printed == check[1] else
                           "the block states it prints %r; it printed %r" % (check[1], printed))
            else:
                exec(compile(ast.Module([stmt], type_ignores=[]), "README.md", "exec", dont_inherit=True), ns)
                continue
        except BaseException as e:                 # noqa: B902 -- report whatever a reader would see
            tb = traceback.extract_tb(e.__traceback__)
            at = [f for f in tb if f.filename == "README.md"]
            line = at[-1].lineno if at else stmt.lineno
            sys.stderr.write("FAILED README.md:L%d: %s: %s\n" % (line, type(e).__name__, e))
            traceback.print_exception(type(e), e, e.__traceback__)
            return 1
        if problem:
            wrong += 1
            sys.stderr.write("FAILED README.md:L%d: %s\n" % (check[2] + offset, problem))
        else:
            held += 1
    sys.stderr.write("%d stated result(s) held, %d did not\n" % (held, wrong))
    return 1 if wrong else 0


# ── running blocks (from the test suite or the command line) ────────────────────────────────────────
class Result:
    def __init__(self, block, returncode, stdout, stderr):
        self.block, self.returncode, self.stdout, self.stderr = block, returncode, stdout, stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def failure(self) -> str:
        """Every `FAILED README.md:L<n>: ...` line, or the tail of stderr when the runner died first."""
        lines = [ln[len("FAILED "):] for ln in self.stderr.splitlines() if ln.startswith("FAILED ")]
        if lines:
            return "; ".join(lines)
        return (self.stderr.strip().splitlines() or ["exit %d, no output" % self.returncode])[-1]


def isolated_env(home: str, python: "str | None") -> dict:
    """The environment a block runs in: HOME and the config home under `home`, so receipt heads and
    keys never reach the real ~/.config, and no key directory from the caller's shell."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "INSPEXIMUS_KEY_HOME", "APPDATA", "XDG_CONFIG_HOME", "PYTHONHOME")}
    env.update({"HOME": home, "USERPROFILE": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    if python is None:                                 # no interpreter given: test THIS checkout
        env["PYTHONPATH"] = ROOT
    return env


def run_block(block: Block, cwd: str, home: str, python: "str | None" = None, timeout: int = 300) -> Result:
    """Run one block with `cwd` as its working directory. `python` is an interpreter with inspeximus
    installed; None means this interpreter, importing this checkout."""
    src_dir = tempfile.mkdtemp(prefix="readme_block_src_")
    src = os.path.join(src_dir, "block.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(block.code)
    try:
        p = subprocess.run([python or sys.executable, os.path.abspath(__file__), "--exec", src,
                            "--first-line", str(block.line + 1)],
                           cwd=cwd, env=isolated_env(home, python), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)
    return Result(block, p.returncode, p.stdout, p.stderr)


@functools.lru_cache(maxsize=None)
def has_module(module: str, python: "str | None" = None) -> bool:
    """Can the interpreter under test import `module`? Asked by importing it in that interpreter, not by
    finding it: a package that is installed and fails on import is, to a reader, not installed."""
    env = isolated_env(tempfile.gettempdir(), python)
    return subprocess.run([python or sys.executable, "-c", "import " + module], capture_output=True,
                          env=env, cwd=tempfile.gettempdir()).returncode == 0


def missing_extras(extras, python: "str | None" = None) -> list:
    """The declared extras whose module the interpreter under test cannot import."""
    unknown = [e for e in extras if e not in EXTRA_MODULE]
    if unknown:
        raise ValueError("declared extra(s) %s: add the module each brings to EXTRA_MODULE" % unknown)
    return [e for e in extras if not has_module(EXTRA_MODULE[e], python)]


def verdict(result: Result, missing: list) -> "str | None":
    """None when the block did what it should, else why not.

    With every declared extra present, the block must run and every stated result must hold. With one
    missing, it must fail and say which module it needed; a pass means the declaration is stale."""
    if not missing:
        return None if result.ok else result.failure
    if result.ok:
        return ("declares inspeximus[%s] but runs without %s; drop the declaration"
                % (",".join(missing), ", ".join(EXTRA_MODULE[e] for e in missing)))
    text = result.stdout + result.stderr
    unnamed = [EXTRA_MODULE[e] for e in missing if EXTRA_MODULE[e] not in text]
    if unnamed:
        return "fails without %s but never names it: %s" % (", ".join(unnamed), result.failure)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--python", default=None, help="an interpreter with inspeximus installed "
                    "(default: this interpreter, importing this checkout)")
    ap.add_argument("--layout", choices=["fresh", "shared", "both"], default="both")
    ap.add_argument("--readme", default=README)
    ap.add_argument("--exec", dest="exec_path", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--first-line", type=int, default=1, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.exec_path:
        return _exec_block(a.exec_path, a.first_line)

    bs = blocks(a.readme)
    layouts = ["fresh", "shared"] if a.layout == "both" else [a.layout]
    failed = 0
    for layout in layouts:
        base = tempfile.mkdtemp(prefix="readme_blocks_%s_" % layout)
        print("--- %s: %s" % (layout, "each block in its own empty directory" if layout == "fresh"
                                   else "all blocks in order, in one directory"))
        for i, b in enumerate(bs):
            where = os.path.join(base, "block%02d" % i if layout == "fresh" else "shared")
            os.makedirs(os.path.join(where, "work"), exist_ok=True)
            os.makedirs(os.path.join(where, "home"), exist_ok=True)
            missing = missing_extras(b.extras, a.python)
            why = verdict(run_block(b, os.path.join(where, "work"), os.path.join(where, "home"), a.python),
                          missing)
            note = " (without %s: must fail)" % ",".join(missing) if missing else ""
            print("%-18s %s%s" % (b.name, "ok" if why is None else "FAIL  " + why, note))
            failed += why is not None
        shutil.rmtree(base, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
