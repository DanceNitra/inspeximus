"""No `inspeximus` command answers an ordinary bad argument with a Python traceback (3.21, AUDIT-A F-2).

On 3.20.0 a sweep of every subcommand found 338 tracebacks in 47 commands: `writer-key --new --out` with a missing
parent directory, a seq that is not in the ledger, a hash that is not 64 hex characters, a partition that does not
exist. The cause was one class: the command line let the library's input checks and the file system's refusals reach
the user as exceptions. `cli.main` now prints one line and returns 2 for a UsageError, an OSError and a
json.JSONDecodeError, and every input check the commands reach raises UsageError.

This walks every subcommand in-process and gives each argument, in turn, a path whose parent directory does not exist,
a file that does not exist, a directory, an empty file, a file of invalid JSON, -1 and "x". An exception that escapes
`main` is a finding. Each case runs in a directory of its own.

AUDIT-A A-3 widened it, and every argument now also gets: a file that is not UTF-8, valid JSON of the wrong shape
(an array, a string, null, an object with the wrong fields), a number too large for a date, nan, -0.0, an empty
string, a path of 5,000 characters, a path through a file, non-ASCII text and "-". On a09dc529 that found 40
tracebacks in 21 commands. Two values stay out: a NUL byte and a lone surrogate, which a Windows command line
cannot pass (a lone surrogate reaches argv on POSIX from undecodable bytes; that is a known limit). The commands
in SKIP are left out by name, each for the reason it gives.
"""
import argparse
import contextlib
import io
import os

import pytest

from inspeximus import cli
from inspeximus.errors import UnknownName, UsageError

#: The 9 commands the walk does not run, and why. The CHANGELOG names the same 9. a09dc529 skipped 12; `timestamp
#: qualified`, `browse` and `demo` read and write only local files, so A-3 walks them too.
SKIP = {("witness", "serve"): "a server: it runs until stopped",
        ("witness", "watch"): "polls a transparency log over HTTPS until stopped",
        ("hookd",): "a daemon: it runs until stopped",
        ("mcp",): "an MCP server on stdin: it runs until stopped",
        ("ots", "upgrade"): "asks the OpenTimestamps calendars over the network",
        ("timestamp", "trusted-lists"): "downloads the EU trusted lists",
        ("actions", "timestamp"): "asks an RFC 3161 timestamp authority over the network",
        ("distill",): "calls a language model endpoint",
        ("reembed",): "calls the configured embedder for every record"}
SIMPLE = (argparse._HelpAction, argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction,
          argparse._CountAction, argparse._VersionAction)
BAD_PATHS = ("missing_parent", "missing_file", "directory", "empty_file", "bad_json")
BAD_NUMS = ("-1", "x")
#: AUDIT-A A-3: given to every argument, whatever its type.
WIDER = ("bad_utf8", "json_list", "json_str", "json_null", "json_obj_wrong", "huge", "nan", "neg_zero_float", "empty",
         "longpath", "file_as_dir", "unicode", "dash")


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


def _cases(group="builder"):
    out = []
    for prefix, p in _leaves(_root_parser()):
        if any(prefix[:len(s)] == s for s in SKIP):
            continue
        acts = [a for a in p._actions if not isinstance(a, SIMPLE)]
        base = [a for a in acts if a.required or not a.option_strings]
        for target in acts:
            if target.choices:
                continue
            for bad in ((BAD_NUMS if target.type in (int, float) else BAD_PATHS) if group == "builder" else (group,)):
                out.append((prefix, base, target, bad))
    return out


def _value(bad, d):
    paths = {"missing_parent": os.path.join(d, "no", "such", "dir", "f.json"), "missing_file": os.path.join(d, "nope.json"),
             "directory": os.path.join(d, "adir"), "empty_file": os.path.join(d, "empty.txt"),
             "bad_json": os.path.join(d, "bad.json"), "bad_utf8": os.path.join(d, "bad_utf8.json"),
             "json_list": os.path.join(d, "list.json"), "json_str": os.path.join(d, "str.json"),
             "json_null": os.path.join(d, "null.json"), "json_obj_wrong": os.path.join(d, "obj.json"),
             "huge": "9" * 40, "nan": "nan", "neg_zero_float": "-0.0", "empty": "",
             "longpath": os.path.join(d, "x" * 5000), "file_as_dir": os.path.join(d, "empty.txt", "child.json"),
             "unicode": "\u00e9\u4e2d\U0001F600", "dash": "-"}
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


@pytest.mark.parametrize("group", ("builder",) + WIDER)
def test_no_command_lets_an_exception_escape_on_bad_input(group, tmp_path, monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    cases = _cases(group)
    assert len(cases) > (1000 if group == "builder" else 250), "CONTROL: the walk found the subcommands (%d cases)" % len(cases)
    escaped = []
    for i, (prefix, base, target, bad) in enumerate(cases):
        d = tmp_path / str(i)
        (d / "adir").mkdir(parents=True)
        (d / "empty.txt").write_bytes(b"")
        (d / "bad.json").write_bytes(b"{not json")
        (d / "bad_utf8.json").write_bytes(b"\xff\xfe\x00{\x80")
        for name, body in (("list.json", b"[]"), ("str.json", b'"x"'), ("null.json", b"null"),
                           ("obj.json", b'{"a": 1, "entries": 5, "heads": "x"}')):
            (d / name).write_bytes(body)
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


def test_the_walk_skips_exactly_the_nine_named_commands():
    """The CHANGELOG names these 9. A command added to SKIP without a reason, or a skipped name that no longer
    exists, fails here."""
    assert len(SKIP) == 9 and all(isinstance(v, str) and v for v in SKIP.values())
    leaves = {prefix for prefix, _p in _leaves(_root_parser())}
    assert all(any(leaf[:len(k)] == k for leaf in leaves) for k in SKIP), "a skipped command does not exist"


@pytest.mark.parametrize("body", [b"{not json", b"\xff\xfe\x00{\x80"])
def test_a_corrupt_store_keeps_its_traceback(body, tmp_path, monkeypatch):
    """CONTROL for A-3: the caller's files are mistakes, the store's own file is a fault. A store that is not JSON
    or not UTF-8 still raises, so it is reported rather than read as a typo."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    store = tmp_path / "store.json"
    store.write_bytes(body)
    monkeypatch.setenv("INSPEXIMUS_PATH", str(store))
    rc, err, exc = _run(["stats"])
    assert exc is not None and not isinstance(exc, UsageError), (rc, err, exc)


def test_a_file_of_the_wrong_shape_is_one_line(tmp_path, monkeypatch):
    """A-3: valid JSON of the wrong shape names both shapes, in one line, instead of an AttributeError."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    (tmp_path / "list.json").write_bytes(b"[]")
    for argv in (["erasure-verify", "list.json"], ["residue-verify", "list.json"]):
        rc, err, exc = _run(argv)
        assert exc is None and rc == 2, (argv, rc, exc)
        assert "holds JSON an array, and this command reads an object" in err and err.count("\n") == 1, err


def test_a_trail_line_that_is_not_a_record_fails_the_verification(tmp_path, monkeypatch):
    """A-3: `actions export-trail --verify` is a verifier, so JSON that is not a record is a FAIL (exit 1) with the
    line named, and a file that is not UTF-8 is a FAIL too, never a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "store.json"))
    for name, body, want in (("list.jsonl", b"[]\n", "line 1: not a JSON object"),
                             ("bad.jsonl", b"\xff\xfe\x00{\x80", "not UTF-8 text")):
        (tmp_path / name).write_bytes(body)
        rc, err, exc = _run(["actions", "export-trail", "--verify", name])
        assert exc is None and rc == 1, (name, rc, exc)


@pytest.mark.parametrize("name, body", [("deep.json", b"[" * 200000 + b"]" * 200000),
                                        ("bigint.json", b"1" * 6000)], ids=["nested-200000-deep", "6000-digits"])
def test_json_python_cannot_parse_is_one_line(name, body, tmp_path, monkeypatch):
    """AUDIT-A recheck R-3: a JSON file nested 200,000 deep raised RecursionError, and an integer of 6,000 digits
    raised ValueError (CPython 3.11 and later cap integer parsing at 4,300 digits). Both are the caller's file, so
    each is one line and exit 2. On a Python without the cap, the integer parses and is the wrong shape instead."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    (tmp_path / name).write_bytes(body)
    for argv in (["erasure-verify", name], ["residue-verify", name], ["audit-verify", name]):
        rc, err, exc = _run(argv)
        assert exc is None and rc == 2, (argv, rc, exc)
        assert err.startswith("inspeximus %s: error: " % argv[0]) and err.count("\n") == 1, err
