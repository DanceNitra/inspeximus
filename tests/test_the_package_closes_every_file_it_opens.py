"""No module of the package opens a file without closing it (the ResourceWarning sweep, AUDIT-A).

`open(p).read()` and `json.load(open(p))` close the file when CPython drops the last reference, so no
handle stays open, but each one emits a ResourceWarning. That breaks a caller who runs with
`-W error::ResourceWarning`, and it made a reproducer read an unrelated warning as the answer it was
looking for. 25 sites in seven modules had the shape on 3.15.5. The fix is `Path.read_text`,
`Path.read_bytes` and `Path.write_text`, which close the file themselves; `json.dumps` before the write
also stops a value that cannot be encoded from leaving a half-written file, which `json.dump(x, open(p, "w"))`
did.

This test reads the package's source and finds the shape by syntax, so a new site fails it whichever
module it lands in. The control feeds it the shape and requires it to be found.
"""
import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (file, count) the Builder owns and has not fixed yet. Remove the entry when it is fixed.
OWNED_BY_ANOTHER_SESSION = {"install_all.py": 1}

_OPENERS = ("io", "codecs", "gzip", "bz2", "lzma")


def _is_open(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("open", "_open")
    return (isinstance(f, ast.Attribute) and f.attr == "open" and isinstance(f.value, ast.Name)
            and f.value.id in _OPENERS)


def bare_opens(source: str) -> list:
    """Line numbers of every open() whose file object is used in an expression and never bound by `with`."""
    tree = ast.parse(source)
    guarded = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                guarded.update(id(c) for c in ast.walk(item.context_expr))
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and _is_open(n.value) and id(n.value) not in guarded:
            found.append(n.value.lineno)                                # open(...).read()
        elif isinstance(n, ast.Call):
            found += [a.lineno for a in n.args if _is_open(a) and id(a) not in guarded]   # json.load(open(...))
    return sorted(set(found))


def test_control_the_check_finds_the_shape():
    src = ("import json\n"
           "a = open('x').read()\n"
           "b = json.load(open('x'))\n"
           "json.dump(b, open('x', 'w'))\n"
           "with open('x') as fh:\n"
           "    c = fh.read()\n"
           "with open('x') as fh, open('y') as gh:\n"
           "    d = json.load(fh)\n")
    assert bare_opens(src) == [2, 3, 4], bare_opens(src)


def test_no_module_of_the_package_opens_a_file_it_does_not_close():
    offenders = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "inspeximus", "**", "*.py"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            lines = bare_opens(fh.read())
        name = os.path.relpath(path, os.path.join(ROOT, "inspeximus")).replace(os.sep, "/")
        allowed = OWNED_BY_ANOTHER_SESSION.get(name, 0)
        if len(lines) != allowed:
            offenders[name] = lines
    assert not offenders, (
        "open() without a context manager (use Path.read_text / read_bytes / write_text, or `with`): "
        + "; ".join(f"{n}:{ls}" for n, ls in offenders.items()))
