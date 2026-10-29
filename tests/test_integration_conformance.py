"""Every framework adapter, exercised through the framework's own interface, against a recorded ledger.

WHAT THIS ADDS that the existing per-adapter tests do not. `test_haystack.py`, `test_google_adk.py`,
`test_remaining_integrations.py` and friends each `importorskip` their framework and then test OUR
class. That is necessary and it is not sufficient: `crewai` imports perfectly well in 1.x, and the
`Storage` protocol our adapter implements was deleted from it. An adapter can be green in a suite that
never touches the framework's own types.

So this file drives `tools/integration_conformance.py`, where every adapter's round trip goes IN through
the framework's interface and comes back OUT through it -- a compiled LangGraph, a Haystack `Pipeline`,
a Pydantic AI `Agent`, ADK's `BaseMemoryService`, the OpenAI Agents `Session` protocol.

THREE PROPERTIES, and the third is the one that makes the other two mean anything:

  1. Every module in `inspeximus/integrations/` has a conformance check. A new adapter with no round
     trip turns this red rather than shipping unverified, which is how four of eleven went without one.
  2. Every adapter's live status matches `docs/integration_conformance.json`. That ledger records the
     upstream version each was last verified against, so a breakage can be DATED. Drift in either
     direction is red: an adapter that stops conforming, and an adapter recorded BROKEN that starts
     conforming (which must be recorded, in a diff someone reads, not absorbed).
  3. CONTROL: with the write path neutered, every round trip MUST go BROKEN. A conformance suite nobody
     has seen fail is not a conformance suite. `_NeuteredWrites` replaces `Inspeximus.remember`, which
     every adapter funnels through, so it is an input no round trip can examine its way out of.

A SKIP IS NOT A PASS. The guards are inside the test functions (so this module still collects on a bare
install and stays out of tools/skip_census.py's hidden count), each absent framework skips ONE visible
line, and `test_a_run_that_verified_nothing_is_not_a_pass` pins the exit-code half: the runner refuses
to exit 0 having checked nothing.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import integration_conformance as ic  # noqa: E402

NAMES = [c.name for c in ic.CHECKS]
_RESULTS = {}


def _result(name):
    """One real run per integration, shared across the tests in this file."""
    if name not in _RESULTS:
        _RESULTS[name] = ic.run_check(ic.BY_NAME[name])
    return _RESULTS[name]


def _ledger():
    assert ic.RECORD_PATH.exists(), (
        f"{ic.RECORD_PATH} is missing. Run: python tools/integration_conformance.py --write-record")
    return ic.load_record()["integrations"]


def _require(check):
    if check.module:
        pytest.importorskip(check.module,
                            reason=f"{check.dist or check.module} is an optional extra")


# ── 1. the registry must be total ───────────────────────────────────────────────────────────────────
def test_every_integration_module_has_a_conformance_check():
    """The anti-rot property. Adding `inspeximus/integrations/newframework.py` without a round trip
    here fails, instead of shipping an adapter nothing exercises."""
    covered = {c.source for c in ic.CHECKS}
    on_disk = {p.name for p in (pathlib.Path(ROOT) / "inspeximus" / "integrations").glob("*.py")
               if p.name != "__init__.py"}
    assert on_disk - covered == set(), (
        f"no conformance check for: {sorted(on_disk - covered)}. Add a round trip to "
        f"tools/integration_conformance.py -- an adapter nobody exercises is an adapter nobody knows "
        f"is broken.")
    assert covered - on_disk == set(), f"checks for modules that no longer exist: {sorted(covered - on_disk)}"
    assert len(on_disk) >= 11, f"only {len(on_disk)} integration modules found -- did the glob break?"


def test_every_check_is_in_the_recorded_ledger():
    """Which is what makes a future breakage dateable: without a row there is no version to compare to."""
    missing = [n for n in NAMES if n not in _ledger()]
    assert not missing, (f"not recorded: {missing}. Run: python tools/integration_conformance.py "
                         f"--write-record")


def test_the_ledger_records_an_upstream_version_for_every_framework_adapter():
    """Without a version there is nothing to date a breakage against, which is half the point of the
    file. And exactly one of verified_against / broken_against is set, so no row can read as verified
    against a version it fails on."""
    led = _ledger()
    for c in ic.CHECKS:
        row = led[c.name]
        assert not (row["verified_against"] and row["broken_against"]), row
        if c.module is None:
            # Framework-free by design (governance, memoryagentbench): there is no upstream release to
            # date anything against, and saying so in a note is the honest record. This branch is the
            # one my first version got wrong -- it demanded a version from an adapter that has no
            # upstream, and turned a correct row red.
            assert row["verified_against"] is None and row["broken_against"] is None, row
            assert row["note"], f"{c.name} has no upstream and no note explaining why"
        else:
            assert row["upstream_dist"], row
            assert row["verified_against"] or row["broken_against"], \
                f"{c.name} was recorded with no upstream version, so a breakage cannot be dated: {row}"
            assert (row["verified_against"] is not None) == (row["status"] == ic.VERIFIED), row
        if row["status"] == ic.BROKEN:
            assert row["detail"], f"{c.name} is recorded broken with no diagnosis: {row}"


# ── 2. every adapter, against the recorded status ───────────────────────────────────────────────────
@pytest.mark.parametrize("name", NAMES)
def test_integration_roundtrip_matches_the_recorded_status(name):
    """Drift in EITHER direction is a failure that someone has to read.

    A recorded-verified adapter that breaks is the upstream regression this unit exists to catch. A
    recorded-broken adapter that starts passing is just as loud on purpose: the ledger is the published
    statement of what conforms, and a silent improvement leaves it lying."""
    check = ic.BY_NAME[name]
    _require(check)
    row = _result(name)
    recorded = _ledger()[name]

    if recorded["status"] == ic.VERIFIED:
        assert row["status"] == ic.VERIFIED, (
            f"{name} no longer conforms against {row['upstream_dist']} {row['upstream_version']} "
            f"(last verified against {recorded['verified_against']} on {recorded['checked']}).\n"
            f"{row.get('detail')}\n{row.get('traceback', '')}")
    else:
        assert row["status"] == ic.BROKEN, (
            f"{name} is recorded BROKEN but now passes against {row['upstream_dist']} "
            f"{row['upstream_version']}. That is good news and it must be RECORDED: run "
            f"`python tools/integration_conformance.py --write-record` and commit the ledger.")


@pytest.mark.parametrize("name", NAMES)
def test_the_roundtrip_reads_back_through_the_framework(name):
    """CONTROL, and the reason to believe the test above.

    With `Inspeximus.remember` neutered, the adapter's write goes nowhere while every call still
    returns normally. A round trip that only checked "the call did not raise", or that reached past the
    adapter into the store, would still pass. Every one of these must go BROKEN.

    Recorded-broken adapters are exempt only because they are already failing, so the control cannot
    distinguish the neutered write from the defect already there."""
    check = ic.BY_NAME[name]
    _require(check)
    if _ledger()[name]["status"] != ic.VERIFIED:
        pytest.skip(f"{name} is recorded BROKEN; the control needs a passing round trip to falsify")
    broken = ic.run_check(check, falsify=True)
    assert broken["status"] == ic.BROKEN, (
        f"{name} still passed with the write path neutered, so its round trip does not read back what "
        f"it writes -- it cannot detect a broken adapter and it is not evidence of anything.")


# ── 3. a skip is not a pass ─────────────────────────────────────────────────────────────────────────
def test_a_run_that_verified_nothing_is_not_a_pass(monkeypatch, capsys):
    """The single most likely way this unit goes wrong: on a bare install every framework is absent,
    every check skips, and a runner that counted only failures exits 0 and reads as green."""
    monkeypatch.setattr(ic, "_present", lambda module: False)
    rc = ic.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, "a run that verified nothing must not exit 0"
    assert payload["counts"] == {ic.VERIFIED: 0, ic.SKIPPED: len(NAMES), ic.BROKEN: 0}, payload["counts"]

    assert ic.main([]) == 1                       # the human summary says the same thing out loud
    out = capsys.readouterr().out
    assert "NOTHING WAS VERIFIED" in out, out[-400:]
    assert "VERIFIED 0" in out and f"SKIPPED {len(NAMES)}" in out, out[-400:]


def test_the_summary_reports_the_three_counts_separately(capsys):
    """"9 passed" is the answer that hides this whole failure mode."""
    ic.main(["--only", "governance"])
    out = capsys.readouterr().out
    assert "VERIFIED" in out and "SKIPPED" in out and "BROKEN" in out, out


def test_require_all_turns_a_skip_into_a_failure(monkeypatch, capsys):
    """What the CI leg with the extras installed uses: there, an absent framework is an install bug,
    not an acceptable outcome."""
    real = ic._present
    monkeypatch.setattr(ic, "_present", lambda module: False if module == "haystack" else real(module))
    assert ic.main(["--only", "haystack,governance", "--require-all"]) == 1
    capsys.readouterr()
    assert ic.main(["--only", "haystack,governance"]) == 0, \
        "without --require-all a skip alongside a verified check is tolerated"
    capsys.readouterr()


def test_a_broken_integration_makes_the_runner_exit_nonzero(monkeypatch, capsys):
    def boom(_tmp):
        raise AssertionError("the adapter is broken")
    monkeypatch.setattr(ic.BY_NAME["governance"], "roundtrip", boom)
    rc = ic.main(["--only", "governance"])
    assert rc == 1, capsys.readouterr().out


def test_the_falsify_control_fails_loudly_when_a_roundtrip_cannot_detect_a_broken_adapter(monkeypatch,
                                                                                          capsys):
    """The control's own control. A round trip that asserts nothing survives the neutered write path,
    and the runner must call that out rather than print a pass."""
    monkeypatch.setattr(ic.BY_NAME["governance"], "roundtrip", lambda _tmp: None)
    rc = ic.main(["--only", "governance", "--falsify", "governance"])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "CONTROL FAILED" in out, out[-400:]


def test_presence_probing_does_not_import_the_framework():
    """`_present` must answer for a package that is not installed without raising, and must not be
    fooled by a name that only looks importable."""
    assert ic._present("definitely_not_a_real_package_xyz") is False
    assert ic._present(None) is True
    assert ic._present("json") is True


# ── 4. the moat: zero required dependencies ─────────────────────────────────────────────────────────
def test_importing_inspeximus_needs_no_framework():
    """The registry, the round trips and the runner all live outside the package. If any framework
    import leaked into `inspeximus` itself, this is where it shows."""
    import subprocess
    blocker = (
        "import sys\n"
        "from importlib.abc import MetaPathFinder\n"
        "BAD = ['langgraph','langchain','langchain_core','llama_index','autogen_core',"
        "'autogen_agentchat','google','agents','pydantic_ai','crewai','haystack','mcp']\n"
        "class B(MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if any(name == n or name.startswith(n + '.') for n in BAD):\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import inspeximus\n"
        "from inspeximus.core import Inspeximus\n"
        "s = Inspeximus(path=None)\n"
        "s.remember('zero dependency', key='k', object='v')\n"
        "assert s.recall('zero dependency')\n"
        "print('ok')\n")
    r = subprocess.run([sys.executable, "-c", blocker], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr[-800:]
    assert "ok" in r.stdout


def test_the_runner_itself_imports_with_no_framework_installed():
    """It has to be runnable on a bare install, or the summary that reports the skips never prints."""
    import subprocess
    r = subprocess.run([sys.executable, "tools/integration_conformance.py", "--only", "governance"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    assert "VERIFIED 1" in r.stdout, r.stdout[-600:]


def test_every_framework_is_an_optional_extra_in_pyproject():
    """Each adapter must have a declared install path, and none of them may be a hard requirement.

    `haystack` had no extra at all: `pip install inspeximus[haystack]` failed and CI installed
    `haystack-ai` by hand in one job, so the documented adapter had no supported way to be installed."""
    src = pathlib.Path(ROOT, "pyproject.toml").read_text(encoding="utf-8")
    head = src.split("[project.optional-dependencies]")[0]
    assert "dependencies = [" not in head, "inspeximus must declare no mandatory dependencies"
    extras = src.split("[project.optional-dependencies]")[1].split("[project.scripts]")[0]
    for c in ic.CHECKS:
        if c.dist is None:
            continue
        assert c.dist in extras, (
            f"{c.dist} backs the {c.name} adapter but is not in [project.optional-dependencies]; "
            f"there is no supported way to install it")


#: Words that make a comment a RECORDED REASON rather than a label. A reason says what broke.
_REASON_WORDS = ("because", "breaks", "broke", "fails", "failed", "measured", "does not",
                 "no longer", "incompatible", "regress")


def _unexplained_caps(extras, min_reason_chars=80):
    """Capped requirement lines whose comment block above them does not record a reason.

    Returned as (line, why it is unexplained), so a failure names the defect rather than a diff.
    """
    lines = extras.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if "<" not in ln or ln.strip().startswith("#") or "=" not in ln:
            continue
        reason, j = [], i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            reason.insert(0, lines[j].strip().lstrip("#").strip())
            j -= 1
        text = " ".join(reason)
        if len(text) < min_reason_chars:
            out.append((ln.strip(), "no comment block above it, or too short to be a reason"))
        elif not any(w in text.lower() for w in _REASON_WORDS):
            out.append((ln.strip(), "the comment above it never says what broke"))
    return out


def test_no_upper_bound_is_added_without_a_recorded_reason():
    """A library upper bound has a MEASURED cost here: the `mcp<2` cap changed pip's search order and a
    nine-extra install resolved google-adk ten majors back. So a cap is allowed only alongside the
    breakage that justifies it, written down next to it.

    This used to assert that the capped set equalled one literal string, `mcp = ["mcp[cli]>=1.28,<2"]`.
    That detects CHANGE, not the rule: widening the cap to `<3` fails it, and so does REMOVING the cap,
    which is what the docstring actually asks for. A test that encodes today's answer cannot tell a
    justified cap from an unjustified one, and it blocked the mcp 2.x port for the wrong reason. The
    rule is now enforced as stated, and the controls below make the check able to fail.
    """
    src = pathlib.Path(ROOT, "pyproject.toml").read_text(encoding="utf-8")
    extras = src.split("[project.optional-dependencies]")[1].split("[project.scripts]")[0]

    # CONTROLS, in both directions: the check must flag a bare cap, accept a documented one, and
    # refuse a comment that is merely long. Without these it could pass by never matching anything.
    assert _unexplained_caps('other = ["other<9"]\n'), (
        "CONTROL: a cap with no comment above it must be flagged")
    assert not _unexplained_caps(
        "# other 9 broke the import: the surrounding API moved and the server does not start at\n"
        "# all. Measured on 9.0.0 and 9.1.0, both of which fail at import time.\n"
        'other = ["other<9"]\n'), "CONTROL: a documented cap must be accepted"
    assert _unexplained_caps(
        "# the upper bound for other, carried since the beginning of this project and kept here\n"
        "# so that the extra keeps resolving the way it always has done up to this point.\n"
        'other = ["other<9"]\n'), (
        "CONTROL: a comment that is long but never says what broke is a label, not a reason")
    # And the two conditions must be independent. A three-word note that happens to contain a reason
    # word is still not a reason, and without this control the length condition can be deleted and
    # every test here keeps passing: measured, the mutation survived the whole suite.
    assert _unexplained_caps("# broke\n" 'other = ["other<9"]\n'), (
        "CONTROL: a reason word alone is not a recorded reason")

    unexplained = _unexplained_caps(extras)
    assert not unexplained, (
        "an upper bound carries no recorded reason: %s. Caps are carried only on breakage actually "
        "observed, and the observation is written beside the pin. Prefer a floor." % unexplained)
