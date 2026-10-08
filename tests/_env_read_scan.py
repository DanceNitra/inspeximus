"""The AST scan behind `test_every_environment_variable_has_a_policy.py` (3.18, AUDIT-A EC-5): every read of an
INSPEXIMUS_* value in shipped code goes through `inspeximus/_envpolicy`.

A read is any of these outside `_envpolicy.py`:
  - `.get(NAME)`, `.getenv(NAME)`, `.setdefault(NAME)` or `X[NAME]` (load) with NAME an INSPEXIMUS_* constant;
  - `NAME in X` or `NAME not in X`;
  - a name built at run time that starts with "INSPEXIMUS_" (an f-string, `+`, `%`, `.format`, `.join`);
  - `.startswith("INSPEXIMUS_")`, the scan of the environment by prefix;
  - `os.environ.get(x)`, `os.getenv(x)` or `os.environ[x]` with a name that is not a constant at all, since such a
    read can carry an INSPEXIMUS_* name the scan cannot see. The few that read another program's variables are in
    `DYNAMIC_ALLOWED`, by file and function.
Writes are not reads: `os.environ[NAME] = v`, `pop`, a dict literal's key, and an argument to a call of `_envpolicy`.

And the shape the rules above cannot see, a name held in a variable and read later (`for var in ("INSPEXIMUS_A", ...):
env.get(var)`), is closed from the other side: an INSPEXIMUS_* constant may stand only where it is not a read. That is
an argument of `_envpolicy` or of a wrapper in `WRAPPERS` (which reads through `_envpolicy`), a dict literal's key, the
target of an assignment to a subscript, one side of `==` or `!=`, or a value returned as the name of a source.
"""
from __future__ import annotations

import ast
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = re.compile(r"INSPEXIMUS_[A-Z0-9_]+\Z")
POLICY_MODULE = os.path.join("inspeximus", "_envpolicy.py")
READ_METHODS = {"get", "getenv", "setdefault"}
# (relative file, function) pairs whose dynamic environment read is of a variable that is not ours.
DYNAMIC_ALLOWED: set = set()
# Functions that take a variable's name and read it through `_envpolicy` (or only print it).
WRAPPERS = {"env_url", "env_key", "_flag_from_env", "env_ignored"}


def shipped_files():
    """Every Python file that ships: the package (with its integrations and probes) and the wrapper packages."""
    out = []
    for pat in ("inspeximus/**/*.py", "packages/**/*.py"):
        out += glob.glob(os.path.join(ROOT, pat), recursive=True)
    return sorted(f for f in out if os.sep + "__pycache__" + os.sep not in f)


def _is_envpolicy_call(call):
    f = call.func
    return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("_envpolicy", "_ep")


def _is_environ(node):
    """`os.environ`, a bare `environ`, or `_os.environ`."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ")


def _starts_ours(node):
    """True when `node` builds a string at run time that starts with INSPEXIMUS_."""
    if isinstance(node, ast.JoinedStr) and node.values:
        v = node.values[0]
        return isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.startswith("INSPEXIMUS_")
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        v = node.left
        return isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.startswith("INSPEXIMUS_")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join"):
        v = node.func.value
        return isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.startswith("INSPEXIMUS_")
    return False


def _const_name(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(NAME.match(node.value))


def _allowed_place(node, parent):
    p = parent.get(id(node))
    if isinstance(p, ast.Call):
        f = p.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        return _is_envpolicy_call(p) or (name in WRAPPERS and node in p.args)
    if isinstance(p, ast.Dict):
        return any(k is node for k in p.keys)
    if isinstance(p, ast.Subscript):
        return isinstance(p.ctx, (ast.Store, ast.Del))
    if isinstance(p, ast.Compare):
        return all(isinstance(o, (ast.Eq, ast.NotEq)) for o in p.ops)
    if isinstance(p, ast.Tuple):
        p = parent.get(id(p))
    return isinstance(p, ast.Return)


def violations(path, source=None):
    """[(line, what)] for one file. `source` overrides the file's text (the controls use it)."""
    rel = os.path.relpath(path, ROOT)
    if rel == POLICY_MODULE:
        return []
    tree = ast.parse(source if source is not None else open(path, encoding="utf-8").read())
    funcs = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                funcs.setdefault(id(n), fn.name)
    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[id(c)] = n
    out = []
    for n in ast.walk(tree):
        if _const_name(n) and not _allowed_place(n, parent):
            out.append((n.lineno, "%s stands where it can be read later; read it through _envpolicy" % n.value))
        if isinstance(n, ast.Call):
            if _is_envpolicy_call(n):
                continue
            f = n.func
            meth = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if meth in READ_METHODS and n.args:
                a = n.args[0]
                if _const_name(a):
                    out.append((n.lineno, "reads %s directly" % a.value))
                elif _starts_ours(a):
                    out.append((n.lineno, "reads a computed INSPEXIMUS_ name"))
                elif (meth == "getenv" or (meth == "get" and isinstance(f, ast.Attribute) and _is_environ(f.value))) \
                        and not isinstance(a, ast.Constant) and (rel, funcs.get(id(n))) not in DYNAMIC_ALLOWED:
                    out.append((n.lineno, "reads the environment by a name that is not a constant"))
            if meth == "startswith" and n.args and isinstance(n.args[0], (ast.Constant, ast.Tuple)):
                vals = n.args[0].elts if isinstance(n.args[0], ast.Tuple) else [n.args[0]]
                if any(isinstance(v, ast.Constant) and v.value == "INSPEXIMUS_" for v in vals):
                    out.append((n.lineno, "scans names by the INSPEXIMUS_ prefix"))
        elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
            s = n.slice
            if _const_name(s):
                out.append((n.lineno, "reads %s by subscript" % s.value))
            elif _starts_ours(s):
                out.append((n.lineno, "reads a computed INSPEXIMUS_ name by subscript"))
            elif _is_environ(n.value) and not isinstance(s, ast.Constant) \
                    and (rel, funcs.get(id(n))) not in DYNAMIC_ALLOWED:
                out.append((n.lineno, "reads the environment by a name that is not a constant"))
        elif isinstance(n, ast.Compare) and _const_name(n.left) and any(isinstance(o, (ast.In, ast.NotIn))
                                                                        for o in n.ops):
            out.append((n.lineno, "tests %s for presence" % n.left.value))
    return sorted(set(out))


def all_violations():
    out = []
    for f in shipped_files():
        for line, what in violations(f):
            out.append("%s:%d: %s" % (os.path.relpath(f, ROOT), line, what))
    return out


if __name__ == "__main__":
    for v in all_violations():
        print(v)
