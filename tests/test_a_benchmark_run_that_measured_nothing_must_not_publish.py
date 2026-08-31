"""Two ways this benchmark used to write a number nothing had measured.

Both were reported by @mioimotoai-lgtm in issue #1, in the same sentence as the crash we did fix:
"validate the selected systems before starting the cases, and emit an actionable error instead of
retrying unauthenticated judge calls". We shipped the crash fix and left these, then nearly closed
the issue claiming everything was done.

They matter more than the crash. A crash is loud. These two exit 0 and leave a file behind:

  `--systems bogus` matched no branch, so `out` stayed empty and the artifact was still written with
  n=20 and results={}. A typo produced a file claiming twenty cases and holding none.

  A bad OPENAI_API_KEY made six retries return None. `(openai_chat(...) or "").lower()` turned that
  into an empty string, neither candidate token appeared in it, and the case scored `other`. So
  `errors` reported 0, every case looked like an honest "unclear", and `revert_success_rate: 0.0`
  went into the file tagged `comparable_with_published: true` that the site's figures cite.

That second one is the defect this benchmark exists to find in other people's systems.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

PROBE = os.path.join(os.path.dirname(__file__), "..", "probes", "integrity_bench_revert.py")


def _probe(monkeypatch, key=""):
    """Import the probe fresh, with the environment fixed before its module body runs.

    The key is read at import time into a module global, so setting it afterwards changes nothing.
    A test that patched it later would exercise a code path that cannot occur.
    """
    monkeypatch.setenv("OPENAI_API_KEY", key)
    spec = importlib.util.spec_from_file_location("bench_under_test", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(module, local):
    stem = "integrity_bench_revert_result" + ("_localjudge" if local else "")
    return os.path.join(os.path.dirname(os.path.abspath(PROBE)), stem + ".json")


# -- the selection is checked before any case runs ---------------------------------------------------
@pytest.mark.parametrize("systems", ["bogus", "inspeximus,bogus", "", "  ,  "])
def test_an_unknown_system_refuses_before_it_can_write_anything(monkeypatch, systems, capsys):
    module = _probe(monkeypatch)
    before = None
    path = _artifact(module, local=True)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            before = fh.read()

    code = module.main(["--systems", systems, "--judge", "local"]) if _takes_argv(module) \
        else _run_with_argv(module, ["--systems", systems, "--judge", "local"])
    assert code == 2, "an unknown system must refuse, not run"

    err = capsys.readouterr().err
    assert "unknown system" in err
    assert "inspeximus" in err, "the error must name what IS valid"

    if before is not None:
        with open(path, "rb") as fh:
            assert fh.read() == before, "a refused run rewrote the artifact"


def test_every_known_system_is_actually_dispatched(monkeypatch):
    """The guard and the dispatch must not drift apart.

    A list of valid names that no branch honours would refuse good input; a branch with no entry in
    the list is unreachable through the CLI. Both are silent, so the source is checked.
    """
    module = _probe(monkeypatch)
    source = open(PROBE, encoding="utf-8").read()
    for name in module.KNOWN_SYSTEMS:
        assert '"%s" in want' % name in source, "%s is offered but never dispatched" % name


# -- a judge that could not answer is not a judge that said "unclear" ---------------------------------
def test_a_failed_judge_call_scores_error_and_not_other(monkeypatch):
    module = _probe(monkeypatch, key="sk-not-a-real-key")
    monkeypatch.setattr(module, "JUDGE", "openai")
    monkeypatch.setattr(module, "openai_chat", lambda *a, **k: None)

    verdict = module.judge_current("cache region", "the cache region is osaka", "osaka", "malmo")
    assert verdict == "error", (
        "a judge that never answered was scored as an honest 'unclear', which is how a bad key "
        "became revert_success_rate 0.0")


def test_a_judge_that_answers_unclear_still_scores_other(monkeypatch):
    """The control for the test above. If everything became `error`, the distinction would be gone in
    the other direction and a genuinely confused system would look like a broken judge."""
    module = _probe(monkeypatch, key="sk-not-a-real-key")
    monkeypatch.setattr(module, "JUDGE", "openai")
    monkeypatch.setattr(module, "openai_chat", lambda *a, **k: "unclear")

    assert module.judge_current("cache region", "nothing useful", "osaka", "malmo") == "other"


def test_errors_are_counted_as_errors_in_the_score(monkeypatch):
    module = _probe(monkeypatch)
    cases = [("e", "a", "b", "revert")] * 3
    got = module.score("x", ["error", "error", "error"], cases)
    assert got["errors"] == 3
    assert got["other"] == 0
    assert got["n"] == 0, "a case the judge could not read is not a case that was measured"


# -- a run that measured nothing must leave no file ---------------------------------------------------
def test_a_run_where_every_case_errored_refuses_to_write(monkeypatch, capsys):
    module = _probe(monkeypatch, key="sk-not-a-real-key")
    monkeypatch.setattr(module, "openai_chat", lambda *a, **k: None)
    path = _artifact(module, local=False)
    before = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            before = fh.read()

    code = _run_with_argv(module, ["--systems", "inspeximus", "--n", "2"])
    assert code == 1, "a run that measured nothing must not exit 0"
    assert "REFUSING to write" in capsys.readouterr().err

    if before is not None:
        with open(path, "rb") as fh:
            assert fh.read() == before, (
                "the published artifact was overwritten by a run whose judge never answered")


def test_the_good_path_still_writes_and_exits_zero(monkeypatch, tmp_path, capsys):
    """The control that keeps the guards above from becoming a way to never publish anything.

    --out-dir is not decoration. Without it this test ran the probe with --n 3 and REWROTE the
    committed receipt, so `probes/integrity_bench_revert_result_localjudge.json` went to public main
    saying n=3 while the figure it backs was measured at n=20. A test that publishes its own fixture
    over a real one is worse than no test.
    """
    module = _probe(monkeypatch)
    code = _run_with_argv(module, ["--systems", "inspeximus", "--judge", "local", "--n", "3",
                                   "--out-dir", str(tmp_path)])
    assert code == 0, capsys.readouterr().err
    with open(tmp_path / "integrity_bench_revert_result_localjudge.json", encoding="utf-8") as fh:
        got = json.load(fh)
    assert got["results"]["inspeximus"]["n"] == 3
    assert got["comparable_with_published"] is False


def test_the_committed_receipt_is_not_touched_by_a_test_run(monkeypatch, tmp_path):
    """The control for the control. If --out-dir stopped being honoured, the test above would still
    pass while quietly rewriting the published artifact again."""
    module = _probe(monkeypatch)
    published = _artifact(module, local=True)
    before = open(published, "rb").read() if os.path.exists(published) else None
    _run_with_argv(module, ["--systems", "inspeximus", "--judge", "local", "--n", "2",
                            "--out-dir", str(tmp_path)])
    if before is not None:
        assert open(published, "rb").read() == before, "a test run rewrote the committed receipt"


def test_a_partly_failed_run_reports_what_it_measured(monkeypatch, tmp_path):
    """THE CASE THE FIRST VERSION OF THIS GUARD MISSED, and the reason it is here.

    The guard fired only at n == 0. With 19 of 20 erroring it wrote revert_success_rate 1.0 from a
    single case, errors 19, a top-level n of 20 and comparable_with_published true, at exit 0. The
    boundary was guarded; the class was not.
    """
    module = _probe(monkeypatch, key="sk-not-a-real-key")
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return "osaka" if calls["n"] == 1 else None

    monkeypatch.setattr(module, "openai_chat", flaky)
    code = _run_with_argv(module, ["--systems", "inspeximus", "--n", "5",
                                   "--out-dir", str(tmp_path)])
    assert code == 0, "one usable case is still a measurement; it must not be blocked"
    with open(tmp_path / "integrity_bench_revert_result.json", encoding="utf-8") as fh:
        got = json.load(fh)
    assert got["cases_requested"] == 5
    assert got["cases_measured"] == {"inspeximus": 1}, "the artifact claimed cases it never scored"
    assert got["errors_total"] == 4
    assert got["comparable_with_published"] is False, (
        "a run with four failed judge calls was labelled comparable with the published figures")


def test_a_clean_run_is_still_marked_comparable(monkeypatch, tmp_path):
    """The other direction. If every run were now incomparable, the field would carry no information
    and the openai arm could never publish."""
    module = _probe(monkeypatch, key="sk-not-a-real-key")
    monkeypatch.setattr(module, "openai_chat", lambda *a, **k: "osaka")
    _run_with_argv(module, ["--systems", "inspeximus", "--n", "3", "--out-dir", str(tmp_path)])
    with open(tmp_path / "integrity_bench_revert_result.json", encoding="utf-8") as fh:
        got = json.load(fh)
    assert got["errors_total"] == 0
    assert got["comparable_with_published"] is True


# -- helpers ------------------------------------------------------------------------------------------
def _takes_argv(module):
    import inspect
    return bool(inspect.signature(module.main).parameters)


def _run_with_argv(module, argv):
    old = sys.argv
    sys.argv = ["integrity_bench_revert.py"] + argv
    try:
        return module.main()
    finally:
        sys.argv = old
