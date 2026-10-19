"""No `inspeximus` command answers an ordinary bad argument with a Python traceback (3.21, AUDIT-A F-2).

On 3.20.0 a sweep of every subcommand found 338 tracebacks in 47 commands: `writer-key --new --out` with a missing
parent directory, a seq that is not in the ledger, a hash that is not 64 hex characters, a partition that does not
exist. The cause was one class: the command line let the library's input checks and the file system's refusals reach
the user as exceptions. `cli.main` now prints one line and returns 2 for a UsageError, an OSError and a
json.JSONDecodeError, and every input check the commands reach raises UsageError.

This walks every subcommand in-process and gives each argument, in turn, a path whose parent directory does not exist,
a file that does not exist, a directory, an empty file, a file of invalid JSON, -1 and "x". An exception that escapes
`main` is a finding. Servers, network calls and daemons are left out by name. Each case runs in a directory of its own.
"""
import argparse
import contextlib
import io
import os

import pytest

from inspeximus import cli
from inspeximus.errors import UnknownName, UsageError

SKIP = {("witness", "serve"), ("witness", "watch"), ("ots", "upgrade"), ("distill",), ("hookd",), ("reembed",),
        ("timestamp", "qualified"), ("timestamp", "trusted-lists"), ("demo",), ("browse",), ("mcp",), ("actions", "timestamp")}
SIMPLE = (argparse._HelpAction, argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction,
          argparse._CountAction, argparse._VersionAction)
BAD_PATHS = ("missing_parent", "missing_file", "directory", "empty_file", "bad_json")
BAD_NUMS = ("-1", "x")


def _root_parser():
    captured = []

    class _Got(Exception):
        pass

    real = argparse.ArgumentParser.parse_args

    def grab(self, *a, **k):
        captured.append(self)
        raise _Got()
    argparse.ArgumentParser.parse_args = grab
    try:
        cli.main(["stats"])
    except _Got:
        pass
    finally:
        argparse.ArgumentParser.parse_args = real
    return captured[0]


def _leaves(parser, prefix=()):
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield prefix, parser
        return
    for a in subs:
        for name, p in a.choices.items():
            yield from _leaves(p, prefix + (name,))


def _cases():
    out = []
    for prefix, p in _leaves(_root_parser()):
        if any(prefix[:len(s)] == s for s in SKIP):
            continue
        acts = [a for a in p._actions if not isinstance(a, SIMPLE)]
        base = [a for a in acts if a.required or not a.option_strings]
        for target in acts:
            if target.choices:
                continue
            for bad in (BAD_NUMS if target.type in (int, float) else BAD_PATHS):
                out.append((prefix, base, target, bad))
    return out


def _value(bad, d):
    paths = {"missing_parent": os.path.join(d, "no", "such", "dir", "f.json"), "missing_file": os.path.join(d, "nope.json"),
             "directory": os.path.join(d, "adir"), "empty_file": os.path.join(d, "empty.txt"),
             "bad_json": os.path.join(d, "bad.json")}
    return paths.get(bad, bad)


def _fill(a, d):
    if a.choices:
        return [str(next(iter(a.choices)))]
    if a.nargs in ("+", "*") or (isinstance(a.nargs, int) and a.nargs > 1):
        return ["x"] * (a.nargs if isinstance(a.nargs, int) else 1)
    return [os.path.join(d, "store.json")] if "path" in a.dest or "store" in a.dest else ["x"]


def _run(argv):
    """main(argv) in-process: (return code, stderr, the exception that escaped or None)."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
    except SystemExit as e:
        return e.code, err.getvalue(), None
    except BaseException as e:                                           # noqa: BLE001
        return None, err.getvalue(), e
    return rc, err.getvalue(), None


def test_no_command_lets_an_exception_escape_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    cases = _cases()
    assert len(cases) > 1000, "CONTROL: the walk found the subcommands (%d cases)" % len(cases)
    escaped = []
    for i, (prefix, base, target, bad) in enumerate(cases):
        d = tmp_path / str(i)
        (d / "adir").mkdir(parents=True)
        (d / "empty.txt").write_bytes(b"")
        (d / "bad.json").write_bytes(b"{not json")
        monkeypatch.chdir(d)
        monkeypatch.setenv("INSPEXIMUS_PATH", str(d / "store.json"))
        argv = list(prefix)
        for a in base:
            if a is not target:
                argv += ([a.option_strings[0]] if a.option_strings else []) + _fill(a, str(d))
        argv += ([target.option_strings[0]] if target.option_strings else []) + [_value(bad, str(d))]
        _rc, _err, exc = _run(argv)
        if exc is not None:
            escaped.append("%s %s=%s: %s: %s" % (" ".join(prefix), (target.option_strings or [target.dest])[0], bad,
                                                type(exc).__name__, str(exc)[:120]))
    assert not escaped, "%d cases answered with a traceback:\n%s" % (len(escaped), "\n".join(escaped[:40]))


def test_an_input_mistake_is_one_line_and_exit_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    rc, err, exc = _run(["writer-key", "--new", "--out", str(tmp_path / "no" / "such" / "k.txt")])
    assert exc is None and rc == 2, (rc, exc)
    assert err.startswith("inspeximus writer-key: error: ") and err.count("\n") == 1, err


def test_a_defect_keeps_its_traceback(tmp_path, monkeypatch):
    """The CONTROL for the walk above: an exception that is not an input mistake escapes main."""
    monkeypatch.chdir(tmp_path)

    def broken(*a, **k):
        raise ValueError("a defect, not the caller's mistake")
    monkeypatch.setattr(cli, "_main", broken)
    rc, err, exc = _run(["stats"])
    assert isinstance(exc, ValueError), (rc, err, exc)


def test_inspeximus_traceback_keeps_the_traceback_of_a_handled_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INSPEXIMUS_TRACEBACK", "1")
    rc, err, exc = _run(["audit-verify", str(tmp_path / "nope.json")])
    assert isinstance(exc, FileNotFoundError), (rc, err, exc)


@pytest.mark.parametrize("cmd", [["mcp"], ["witness", "serve"]])
def test_a_command_that_runs_until_stopped_keeps_its_traceback(cmd, monkeypatch):
    seen = {"cmd": tuple(cmd)}

    def fails(argv, s):
        s.update(seen)
        raise OSError("a running service failed")
    monkeypatch.setattr(cli, "_main", fails)
    _rc, _err, exc = _run(cmd)
    assert isinstance(exc, OSError), exc


def test_the_input_errors_are_still_the_exceptions_callers_caught_before():
    assert issubclass(UsageError, ValueError)
    assert issubclass(UnknownName, KeyError) and issubclass(UnknownName, UsageError)
    assert str(UnknownName("no partition named 'x'")) == "no partition named 'x'"
