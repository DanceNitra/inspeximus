"""tools/full_suite.py runs the suite in two steps, and no test may fall between them (3.22, EM 2026-10-20).

Step 1 is the suite without the cited probes, step 2 is the cited probes. A run that splits the suite can go green by
running less of it, so the two steps must collect exactly the node ids a plain collection does, each once. A failure
in step 2 may be load, so it runs once more serially, and only that second result counts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import full_suite as fs  # noqa: E402


def _paths(args):
    return [a for a in args if a == "tests" or a.startswith(("tests/", "--ignore="))]


def test_the_two_steps_collect_every_test_exactly_once():
    whole = fs.collected(["tests"])
    one = fs.collected(_paths(fs.steps(8)[0]))
    two = fs.collected(_paths(fs.steps(8)[1]))
    assert len(whole) > 1000, len(whole)
    assert two and not set(one) & set(two), sorted(set(one) & set(two))[:5]
    assert sorted(one + two) == sorted(whole), (set(whole) - set(one) - set(two), set(one + two) - set(whole))


def test_control_the_serial_list_is_the_cited_probes_only():
    """CONTROL: a file added to SERIAL leaves step 1 and runs apart. That change must be made on purpose."""
    assert fs.SERIAL == ("tests/test_probes_cited_by_docs.py",)


def test_both_steps_name_their_failures_and_skips():
    for step in fs.steps(8):
        assert "-rfEs" in step, step
    assert "-n" in fs.steps(8)[1] and fs.steps(8)[1][fs.steps(8)[1].index("-n") + 1] == "8"


def _xml(path, cases):
    body = "".join('<testcase classname="tests.test_x" name="%s">%s</testcase>'
                   % (name, "<failure message='m'/>" if bad else "") for name, bad in cases)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<testsuites><testsuite tests="%d" failures="%d" errors="0" skipped="0">%s</testsuite></testsuites>'
                 % (len(cases), sum(b for _, b in cases), body))


def _run(monkeypatch, tmp_path, step2, retry, total=4):
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, cwd=None, **kw):
        calls.append(cmd)
        xml = cmd[cmd.index("--junitxml") + 1]
        if "--ignore=tests/test_probes_cited_by_docs.py" in cmd:
            _xml(xml, [("a", False), ("b", False)])
            return R(0)
        if "--dist" in cmd:
            _xml(xml, step2)
            return R(1 if any(b for _, b in step2) else 0)
        _xml(xml, retry)
        return R(1 if any(b for _, b in retry) else 0)
    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    monkeypatch.setattr(fs, "collected", lambda args: ["n%d" % i for i in range(total)])
    monkeypatch.setattr(fs, "wait_for_memory", lambda: 20.0)
    monkeypatch.setattr(fs.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    return fs.main(["--workers", "8"]), calls


def test_a_probe_that_failed_under_load_and_passes_alone_is_green(monkeypatch, tmp_path, capsys):
    rc, calls = _run(monkeypatch, tmp_path, [("c", False), ("d", True)], [("d", False)])
    assert rc == 0, capsys.readouterr().out
    assert calls[-1][3:4] == ["tests/test_x.py::d"] and "-n" in calls[-1] and calls[-1][calls[-1].index("-n") + 1] == "0"


def test_a_probe_that_fails_alone_too_is_red(monkeypatch, tmp_path, capsys):
    rc, _ = _run(monkeypatch, tmp_path, [("c", False), ("d", True)], [("d", True)])
    assert rc == 1 and "NOT GREEN" in capsys.readouterr().out


def test_a_run_that_does_not_add_up_is_red(monkeypatch, tmp_path, capsys):
    """CONTROL: green steps over fewer tests than were collected are not a green run."""
    rc, _ = _run(monkeypatch, tmp_path, [("c", False), ("d", False)], [], total=5)
    assert rc == 1 and "do not add up" in capsys.readouterr().out
