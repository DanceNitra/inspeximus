"""The AST check behind `test_the_environment_reads_through_one_accessor.py` (3.18, AUDIT-A EC-5 and S-1): in shipped
code, only `inspeximus/_envpolicy.py` touches the environment.

A list of read shapes cannot be complete: an alias (`E = os.environ`, `from os import environ as E`, `g = os.getenv`),
a loop over `environ.items()`, a copy (`dict(os.environ)`, `.copy()`), `expandvars`, `environb`, a name built from
pieces. So the rule is about touching, not reading:

  A. Outside `_envpolicy`, no module refers to `environ`, `environb`, `getenv`, `getenvb`, `putenv`, `unsetenv` or
     `expandvars` at all: not as an attribute of anything, not as an import from `os` or `os.path`, not as a bare
     name, and not as a string given to `getattr`. Another program's variables, a child process's environment and the
     few writes go through `_envpolicy.other`, `other_names`, `child_env` and `set_for_this_process`.

  B. An INSPEXIMUS_* name may stand only where it is not read: an argument of a call to `_envpolicy` or of a wrapper in
     `WRAPPERS` (which reads through `_envpolicy`), a dict literal's key, the target of a subscript assignment, one
     side of `==` or `!=`, or a value returned as the name of a source. This closes the one source rule A leaves, a
     copy from `child_env()`: `child_env().get("INSPEXIMUS_PATH")` holds the name in a `.get`.

  C. No string that starts with "INSPEXIMUS_" and is not a whole name is used to build or match one: an f-string, `+`,
     `%`, `.format`, `.join`, `.startswith`, or a comparison (`k[:11] == "INSPEXIMUS_"`).

  Out of scope, on purpose (AUDIT-A delta): deliberate evasion, such as `os.__dict__`, `vars(os)`, `operator.attrgetter`,
  `ctypes`, `exec` or `eval`, or a name built with `chr()`. The check guards against accidents in our own code, not
  against our own code turning hostile.
"""
from __future__ import annotations

import ast
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = re.compile(r"INSPEXIMUS_[A-Z0-9_]+\Z")
POLICY_MODULE = os.path.join("inspeximus", "_envpolicy.py")
TOUCH = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv", "expandvars"}
# Functions that take a variable's name and read it through `_envpolicy` (or only print it).
WRAPPERS = {"env_url", "env_key", "_flag_from_env", "env_ignored"}


def shipped_files():
    """Every Python file that ships: the package (its integrations and probes included) and the wrapper packages under
    `packages/<name>/`. The scripts at the top of `packages/` are release-workflow tools and do not ship."""
    out = glob.glob(os.path.join(ROOT, "inspeximus", "**", "*.py"), recursive=True)
    out += glob.glob(os.path.join(ROOT, "packages", "*", "**", "*.py"), recursive=True)
    return sorted(f for f in out if os.sep + "__pycache__" + os.sep not in f)


def _is_envpolicy_call(call):
    f = call.func
    return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("_envpolicy", "_ep")


def _const_name(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(NAME.match(node.value))


PIECE = re.compile(r"(?i)inspeximus_[a-z0-9_]*\Z")


def _prefix_piece(node):
    """A piece of a name: "INSPEXIMUS_", or a name in another case ("inspeximus_path", for an `.upper()`). A message
    that begins with a name ("INSPEXIMUS_SCOPE=project needs ...") is not a piece."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(PIECE.match(node.value)) \
        and not NAME.match(node.value)


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
    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[id(c)] = n
    out = []
    for n in ast.walk(tree):
        # A: touching the environment
        if isinstance(n, ast.Attribute) and n.attr in TOUCH:
            out.append((n.lineno, "touches .%s; go through _envpolicy" % n.attr))
        elif isinstance(n, ast.Name) and n.id in TOUCH:
            out.append((n.lineno, "touches %s; go through _envpolicy" % n.id))
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in ("os", "posix", "nt"):
            for a in n.names:
                if a.name in TOUCH or a.name == "*":
                    out.append((n.lineno, "imports %s from %s; go through _envpolicy" % (a.name, n.module)))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("getattr", "hasattr") \
                and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and n.args[1].value in TOUCH:
            out.append((n.lineno, "reaches %s through %s; go through _envpolicy" % (n.args[1].value, n.func.id)))
        # B: where an INSPEXIMUS_* name stands
        if _const_name(n) and not _allowed_place(n, parent):
            out.append((n.lineno, "%s stands where it can be read; read it through _envpolicy" % n.value))
        # C: a name built or matched from a piece
        if _prefix_piece(n):
            p = parent.get(id(n))
            built = isinstance(p, (ast.JoinedStr, ast.BinOp)) or (
                isinstance(p, ast.Compare) and n.value.startswith("INSPEXIMUS_")) or (
                isinstance(p, ast.Attribute) and p.attr in ("format", "join")) or (
                isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr == "startswith") or (
                isinstance(p, ast.Tuple) and isinstance(parent.get(id(p)), ast.Call)
                and isinstance(parent.get(id(p)).func, ast.Attribute)
                and parent.get(id(p)).func.attr == "startswith")
            if built:
                out.append((n.lineno, "builds or matches a name from %r; go through _envpolicy" % n.value))
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
