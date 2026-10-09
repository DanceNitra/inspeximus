"""Do these tests have teeth? Break the code on purpose and require them to notice.

A green suite proves nothing about the code; it proves the tests ran. This applies a small edit that a
reader would call a real defect, re-runs the tests, and requires at least one to go red. A mutation that
SURVIVES marks a test that asserts a spelling rather than a behaviour.

WHY THIS IS A FILE AND NOT A SNIPPET
------------------------------------
It existed as an ad-hoc snippet, and the snippet had a bug that inverted its own verdict: it ran pytest
with `-x -rf`. `-x` aborts at the first problem, and `-rf` prints only FAILED to the summary -- never
ERROR. So a mutant killed through a *fixture* (the common shape here, since a broken probe fails at
setup) produced a summary with no FAILED lines, and the harness reported `SURVIVES <<< NO TEETH` for a
mutant its tests had killed four times over.

That is the failure this repository keeps meeting: a check that cannot report the thing it looks for is
indistinguishable from a clean result. A verdict tool is the worst possible place to keep it, because
every downstream conclusion inherits the error silently. Hence: committed, and covered by
`tests/test_mutation_check_harness.py`, which mutates the harness itself.

USAGE
    python tools/mutation_check.py tools/mutations.json
    python tools/mutation_check.py --self-test

Exits non-zero if any mutation survives, so it can gate CI.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_MUTATION_MARK = re.compile(r"^pytestmark\s*=.*\bmark\.mutation\b|^\s*@pytest\.mark\.mutation\b", re.M)


def _marked_mutation(tests: list[str]) -> bool:
    """Does any listed test file carry the `mutation` marker, which pytest.ini deselects by default?"""
    for t in tests:
        path = os.path.join(ROOT, t.split("::", 1)[0])
        try:
            if _MUTATION_MARK.search(io.open(path, encoding="utf-8", errors="replace").read()):
                return True
        except OSError:
            pass
    return False


def _pytest(tests: list[str], env: dict, tb: str = "no") -> subprocess.CompletedProcess:
    # No `-x`: a mutant may break several tests, and stopping early hides which. `-rfE` reports BOTH
    # failures and errors -- an error is a kill, not a crash to be discounted.
    #
    # THE LISTED TESTS MUST ACTUALLY RUN (A-24, 2026-09-27). pytest.ini's addopts carries
    # `-m "not mutation"`, so an entry whose tests are marked `mutation` (the harness's own tests,
    # which edit source in place) collected nothing: pytest exited 5 and this gate read it as "not
    # green before mutating". Nine entries could never be evaluated on any machine, so no full run
    # could ever exit 0. For such an entry the marker filter is lifted for its own tests, and they run
    # serially, as their CI step runs them, because they edit files the other workers would import.
    #
    # ONE PROCESS PER WORKER (session 1, 2026-09-28). pytest.ini's addopts carries `-n auto`, so every
    # pre-flight and every mutant run started its own xdist pool, and mutation_check_parallel with 10
    # workers could start 10 pools of up to 24 processes. Parallelism lives only at the outer level
    # (mutation_check_parallel --workers); inside a worker every pytest run is `-n 0`.
    extra = ["-n", "0"] + (["-m", ""] if _marked_mutation(tests) else [])
    # The PRE-FLIGHT keeps its tracebacks (tb="short"): when it is red, they are the only record of why,
    # and a re-run is exactly what failed to reproduce PC2's two red pre-flights. The mutant run keeps
    # `--tb=no`; only its summary lines are read.
    return subprocess.run(
        [os.environ.get("MUTATION_PYTHON") or sys.executable, "-m", "pytest", *tests, "-q", "--no-header", f"--tb={tb}", "-rfEs", "-p", "no:randomly",
         *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=1800, env=env)


#: What a survivor is re-run against (session 1, 2026-09-28).
_FULL_SUITE = ["tests"]
_OFF = ("", "0", "off", "no", "false")


def _full_suite() -> list:
    """The suite a survivor is classified against, read at call time.

    MUTATION_FULL_SUITE unset: `_FULL_SUITE`. Set to paths (os.pathsep-separated): those. Set to "off":
    none, and a survivor is reported as not classified. It is "off" in two places, both measured the
    first time this ran: inside the full-suite run itself, because suite tests that drive this tool
    with a survivor on purpose started a full-suite run of their own, recursively; and for the whole
    test session (tests/conftest.py), because those same tests otherwise cost a full serial suite each."""
    v = os.environ.get("MUTATION_FULL_SUITE")
    if v is None:
        return list(_FULL_SUITE)
    if v.strip().lower() in _OFF:
        return []
    return [x for x in v.split(os.pathsep) if x]


def _failed_ids(stdout: str) -> list[str]:
    """Full node ids of the FAILED and ERROR summary lines."""
    return sorted({ln.split()[1] for ln in stdout.splitlines()
                   if ln.startswith(("FAILED ", "ERROR ")) and len(ln.split()) > 1})


def _locate(nodeid: str) -> str:
    """A node id pytest printed relative to its rootdir, made runnable from ROOT."""
    path, sep, rest = nodeid.partition("::")
    for base in [ROOT] + [os.path.join(ROOT, s) if not os.path.isabs(s) else s for s in _full_suite()]:
        cand = os.path.join(base, path)
        if os.path.exists(cand):
            return cand + sep + rest
    return nodeid


def _spec_gap(tests: list[str], env: dict, mutated_path: str, mutated: str, original: str) -> list[str]:
    """For a mutant its listed tests did not catch: the tests ELSEWHERE in the suite that do.

    PC2's survivor #428 was a registry gap: the entry listed one test file and an unlisted one kills the
    mutant. A survivor could not tell "the registry lists the wrong tests" from "nothing in the suite
    catches this". So the whole suite runs once against the same mutant, serially (-n 0, as every run
    inside a worker), and each test it reports is re-run on the UNMUTATED code: a test that is red
    without the mutant is not a kill, and dropping it keeps a flaky or already-red test from posing as
    the missing entry. Returns the confirmed killers' node ids, empty when the mutant survives it all,
    and None when classification is off (MUTATION_FULL_SUITE, see `_full_suite`)."""
    suite = _full_suite()
    if not suite:
        return None
    inner = {**env, "MUTATION_FULL_SUITE": "off"}          # no survivor inside this run recurses
    io.open(mutated_path, "w", encoding="utf-8", newline="").write(mutated)
    try:
        full = _pytest(suite, inner)
    finally:
        io.open(mutated_path, "w", encoding="utf-8", newline="").write(original)
    found = [_locate(i) for i in _failed_ids(full.stdout)]
    if not found:
        return []
    clean = _pytest(found, inner)
    red_without = {_locate(i) for i in _failed_ids(clean.stdout)}
    return sorted(i for i in found if i not in red_without)


def _killers(stdout: str) -> list[str]:
    """Which tests noticed. Setup errors count: a fixture that refuses to build IS the test failing."""
    out = []
    for line in stdout.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            out.append(line.split()[1].split("::")[-1])
    return sorted(set(out))


def _ran(stdout: str) -> tuple[int, list[str]]:
    """How many listed tests PASSED, and why any were skipped (from `-rs`).

    A skipped test saw nothing. Before A-26 the gate read "exit 0" as "every listed test ran and none
    noticed", so a mutant whose one catching test needed a missing Playwright browser was called a
    survivor, and a pre-flight in which every listed test skipped was called green.
    """
    passed, reasons = 0, []
    for line in stdout.splitlines():
        if line.startswith("SKIPPED "):
            reasons.append(line.split("]", 1)[-1].strip().split(": ", 1)[-1])
        m = re.search(r"(\d+) passed", line)
        if m and " in " in line:
            passed = int(m.group(1))
    return passed, reasons


def _preflight_red(name: str, pre: subprocess.CompletedProcess) -> str:
    """Why a pre-flight was red, on ONE line, with the full output kept in a file.

    A red pre-flight used to be reported as "tests are not green before mutating" and nothing else: the
    pytest output was discarded, so the failing test and its reason were gone the moment the run ended.
    PC2's full audit of 515a7439 hit two of these, neither reproduced in three re-runs, and there was
    nothing left to diagnose them with. So the reason now names the failing test ids, the exit code and
    pytest's last line, and the whole output goes to a file under MUTATION_PREFLIGHT_DIR (the parallel
    runner points it outside the worker's worktree, which it deletes), else `.mutwt/preflight`.
    One line, because tools/mutation_check_parallel.py reads the reason from a single `skipped:` line.
    """
    ids = sorted({ln.split()[1] for ln in pre.stdout.splitlines()
                  if ln.startswith(("FAILED ", "ERROR ")) and len(ln.split()) > 1})
    lines = [ln.strip() for ln in f"{pre.stderr or ''}\n{pre.stdout or ''}".splitlines() if ln.strip()]
    last = lines[-1] if lines else "no output"
    log = os.environ.get("MUTATION_PREFLIGHT_DIR") or os.path.join(ROOT, ".mutwt", "preflight")
    slug = re.sub(r"[^\w.-]+", "_", name)[:60].strip("_")
    path = os.path.join(log, f"{slug}.{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}.log")
    try:
        os.makedirs(log, exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(f"mutation: {name}\nexit: {pre.returncode}\ncommand: {' '.join(map(str, pre.args))}\n"
                     f"\n--- stdout ---\n{pre.stdout}\n--- stderr ---\n{pre.stderr}\n")
    except OSError as e:
        path = f"not written ({type(e).__name__})"
    return (f"tests are not green before mutating (exit {pre.returncode}; "
            f"failed: {', '.join(ids) if ids else 'no FAILED or ERROR line'}; last line: {last[:200]}; "
            f"full output: {path})")


def _dirty_tracked() -> set:
    """Tracked files git currently reports as modified."""
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                       cwd=ROOT, capture_output=True, text=True)
    return {ln[3:].strip().strip('"') for ln in r.stdout.splitlines() if ln.strip()}


#: Only these may be restored. A probe writes its receipt; nothing else about a run is expected to touch
#: the working tree.
#:
#: The rule used to be the FILENAME shape -- `<something>_result.json` -- and that convention is not one
#: every probe follows. `probes/governance_sufficiency_bytes.json` is written by
#: `governance_sufficiency_probe.py`, did not match, and was therefore left dirty by every run; 45 lines of
#: it (random record ids) were committed as churn in ebabfa8. Worse, dirt that survives a run is dirt the
#: NEXT run records in `dirty_before` and so protects forever, and a receipt a mutant wrote then reads as
#: a developer's own edit. That is how a mutation came to be SKIPPED: `test_the_receipt_still_holds_the
#: _number_we_publish` read an inverted echo_policy receipt, the pre-flight was red, and the run reported
#: 74/75 with the 75th never evaluated.
#:
#: So the rule is now: a `.json` under `probes/` IS a receipt (checked -- all 19 committed ones are written
#: by a probe in that directory), and everything else, including the probe SOURCE that lives beside it, is
#: not. A convention that a fifth of the receipts do not follow is not a rule, it is a coin flip.
_ARTIFACT = re.compile(r"^probes/[\w.-]+\.json$")


def _read_exact(path: str) -> str:
    """Read WITHOUT newline translation, so what comes back can be written back byte-for-byte."""
    return io.open(path, encoding="utf-8", newline="").read()


def _write_exact(path: str, text: str) -> None:
    """Write WITHOUT newline translation.

    In text mode Windows turns every "\\n" into "\\r\\n", so restoring an LF file rewrote it as CRLF --
    content identical, bytes different, file left permanently dirty and reported as unrestorable
    collateral. It hid for a long time because most sources here are CRLF on checkout, which makes the
    round trip an accidental identity; README.md is LF, so it was the one that surfaced. A tool whose job
    is to leave the repository as it found it must put back the bytes it took, not an equivalent
    rendering of them.
    """
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _match_endings(fragment: str, src: str) -> str:
    """Re-render a spec fragment in the line endings the FILE actually uses.

    Specs are authored with "\\n" so one mutations.json serves both a CRLF Windows checkout and an LF
    Linux CI. Only converts when the file is CRLF and the fragment is not already, so a mixed-ending
    file is left alone rather than half-rewritten.
    """
    if "\r\n" in src and "\r\n" not in fragment:
        return fragment.replace("\n", "\r\n")
    return fragment


def _restore(paths) -> list:
    """Undo the RESULT ARTIFACTS this run dirtied -- and nothing else.

    The first version restored every tracked file that became dirty during the run. That is wrong in a way
    that cost real work: a developer editing source WHILE the gate runs in the background looks identical
    to collateral, and `git checkout --` silently threw the edit away. It happened here, to a one-line fix
    in core.py, and the only reason it was caught was a measurement that stopped making sense.

    A tool whose job is to leave the repository as it found it must not be able to delete what it did not
    write. Restricted to probe result files, which is the only collateral a mutation run actually produces.
    """
    restored = []
    for path in sorted(paths):
        if not _ARTIFACT.match(path.replace("\\", "/")):
            continue
        r = subprocess.run(["git", "checkout", "--", path], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            restored.append(path)
    return restored


#: The `platform` values an entry may carry. "posix" means any os.name == "posix"; the others match the
#: start of sys.platform. An entry without the field runs everywhere.
PLATFORMS = ("linux", "win32", "darwin", "posix")


def split_by_platform(mutations: list[dict], plat: str | None = None, osname: str | None = None):
    """(entries that run here, entries that name another platform).

    A mutant on a line only one OS executes (POSIX `flock`, for example) survives on every other OS,
    and a survivor fails this gate, so such an entry could not be registered at all and nothing ran it
    (3.15.6). With `platform` it is registered, runs where its line runs, and is NAMED as not run
    elsewhere rather than dropped. An unknown value is an error: a typo would otherwise send an entry
    to a platform that does not exist, where it never runs."""
    plat = plat or sys.platform
    osname = osname or os.name
    here, elsewhere = [], []
    for m in mutations:
        want = m.get("platform")
        if want is None:
            here.append(m)
            continue
        if want not in PLATFORMS:
            raise ValueError(f"{m.get('name')!r}: platform {want!r} is not one of {PLATFORMS}")
        ok = (osname == "posix") if want == "posix" else plat.startswith(want)
        (here if ok else elsewhere).append(m)
    return here, elsewhere


def report_elsewhere(elsewhere: list[dict], plat: str | None = None) -> None:
    for m in elsewhere:
        print(f"  NOT RUN ON {plat or sys.platform}: {m['name']} (platform {m['platform']})")


def run(mutations: list[dict], verbose: bool = True) -> int:
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    survived, skipped, restored_all = [], [], set()
    why_survived: dict = {}
    # A mutant does not only change code -- the tests it runs execute PROBES, and probes write their
    # result files, which are TRACKED. Restoring only the mutated source left
    # `probes/echo_policy_panel_result.json` holding the mutant's output: safe = 0.00 echo-blocked /
    # 1.00 reaffirm-honored, the exact inverse of the number the shipped docstring publishes, plus three
    # "problems" declaring our own claim wrong. Sitting in the working tree, tracked, one `git add -A`
    # from being published as a receipt. Recording what was ALREADY dirty means a developer's own
    # in-progress edits are never reverted by this.
    dirty_before = _dirty_tracked()
    # Say what we INHERITED. A receipt left dirty by an earlier run is protected by `dirty_before` (it
    # looks exactly like a developer's edit), so it is read by every test in this run and never restored.
    # It cannot be reverted safely from here -- but it must not be silent.
    inherited = sorted(p for p in dirty_before if _ARTIFACT.match(p.replace("\\", "/")))
    if inherited and verbose:
        print(f"  NOTE: {len(inherited)} probe receipt(s) were ALREADY modified before this run and will "
              f"be read as-is: {', '.join(inherited)}\n")

    for mut in mutations:
        name, rel, old, new = mut["name"], mut["file"], mut["old"], mut["new"]
        tests = mut["tests"] if isinstance(mut["tests"], list) else [mut["tests"]]
        path = os.path.join(ROOT, rel)
        src = _read_exact(path)
        # The spec is written with "\n" and stays that way, so one mutations.json works on a Windows
        # checkout (CRLF) and on Linux CI (LF). Reading byte-exactly is what makes this necessary: a
        # multi-line target written with "\n" cannot match a CRLF file, and it does not fail quietly --
        # it becomes a SKIP, and a skip fails this gate. Found immediately, by the first multi-line
        # mutation added after the read was made exact.
        old, new = _match_endings(old, src), _match_endings(new, src)

        # A target that is absent, or present more than once, would mutate nothing or the wrong thing --
        # and either way would report a false verdict rather than an error.
        n = src.count(old)
        if n != 1:
            skipped.append(f"{name}: target appears {n}x in {rel}, not uniquely")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (target appears {n}x)")
            continue

        # Pre-flight: tests that are already red would make every mutant look killed.
        pre = _pytest(tests, env, tb="short")
        if pre.returncode == 5:
            # Exit 5 is "no tests collected". That is a broken entry, not a red test, and reading it
            # as "not green" is how nine entries hid for as long as they did.
            skipped.append(f"{name}: its listed tests collect nothing, so the mutant cannot be judged")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (listed tests collect nothing)")
            continue
        if pre.returncode != 0:
            why = _preflight_red(name, pre)
            skipped.append(f"{name}: {why}")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (not green before mutating)")
                print(f"      {why}")
            continue
        ran, why = _ran(pre.stdout)
        if not ran:
            skipped.append(f"{name}: no listed test ran before mutating ({'; '.join(sorted(set(why)))})")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (no listed test ran)")
            continue

        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            post = _pytest(tests, env)
            killers = _killers(post.stdout)
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(src)
            # RESTORE THE MUTANT'S ARTIFACTS NOW, NOT AT THE END OF THE RUN. The source was always put
            # back per mutation; its RECEIPTS were not -- collateral was collected once, after the whole
            # loop. So a mutant that flips the echo guard writes an INVERTED
            # probes/echo_policy_panel_result.json (safe = 0.00 echo-blocked where we publish 1.00) and
            # that falsified receipt then sits in the tree for every remaining mutation. The pre-flight
            # of a later one reads it, `test_the_receipt_still_holds_the_number_we_publish` goes red,
            # and the mutation is SKIPPED -- measured: 76/77 with the 77th never evaluated, twice.
            # The run's own collateral was changing what its later checks saw. Same window, per mutation.
            for _p in _restore(_dirty_tracked() - dirty_before):
                restored_all.add(_p)

        if killers:
            if verbose:
                print(f"  {name[:58]:58s} -> killed by {', '.join(killers[:3])}"
                      f"{f' (+{len(killers) - 3})' if len(killers) > 3 else ''}")
        elif _ran(post.stdout)[1]:
            # NOT caught, but a listed test did not run against the mutant, and it may be the one that
            # would have. That is not a survival and not a kill: it is an entry this machine cannot
            # judge, and it fails the gate as a skip does (A-26, PC2's three verify-page "survivors").
            why = sorted(set(_ran(post.stdout)[1]))
            skipped.append(f"{name}: NOT EVALUATED, not caught while {len(why)} listed test reason(s) "
                           f"skipped: {'; '.join(why)}")
            if verbose:
                print(f"  {name[:58]:58s} -> NOT EVALUATED (a listed test skipped: {why[0][:60]})")
        else:
            gap = _spec_gap(tests, env, path, src.replace(old, new, 1), src)
            restored_all.update(_restore(_dirty_tracked() - dirty_before))   # the full suite's receipts
            survived.append(name)
            if gap:
                why_survived[name] = ("SURVIVED_SPEC_GAP", "killed outside its listed tests by: " + ", ".join(gap))
                if verbose:
                    print(f"  {name[:58]:58s} -> SURVIVES its listed tests; SPEC GAP, caught by {gap[0]}"
                          f"{f' (+{len(gap) - 1})' if len(gap) > 1 else ''}")
            elif gap is None:
                why_survived[name] = ("SURVIVED", "not classified: MUTATION_FULL_SUITE is off")
                if verbose:
                    print(f"  {name[:58]:58s} -> SURVIVES <<< NO TEETH (not re-run against the full suite)")
            else:
                why_survived[name] = ("SURVIVED", "survives the full suite")
                if verbose:
                    print(f"  {name[:58]:58s} -> SURVIVES <<< NO TEETH (the full suite too)")

    left = _dirty_tracked() - dirty_before
    for _p in _restore(left):                                 # anything a skip path left behind
        restored_all.add(_p)
    if verbose and restored_all:
        print(f"  restored {len(restored_all)} tracked file(s) the run dirtied: "
              f"{', '.join(sorted(restored_all))}")
    # NAME WHAT WE REFUSED TO RESTORE. The allowlist is deliberately narrow -- this tool must never be
    # able to delete work it did not write -- but silence about the remainder is how a mutant reached a
    # committed file today: a test ran the release pinner against the REAL repo, a mutant made that
    # pinner write a wrong field, and `.claude-plugin/marketplace.json` was left corrupted with nothing
    # in the output to say so. Restoring it automatically would be the worse bug; saying nothing was
    # the one we had. The test was fixed to run against a copy; this makes the next one visible.
    unrestored = sorted(p for p in left if p not in restored_all)
    if unrestored:
        print(f"  !! {len(unrestored)} tracked file(s) were dirtied by this run and are OUTSIDE the "
              f"restore allowlist -- check them by hand: {', '.join(unrestored)}")

    if verbose:
        total = len(mutations)
        print(f"\n{total - len(survived) - len(skipped)}/{total} killed, "
              f"{len(survived)} survived, {len(skipped)} skipped")
        for s in skipped:
            print(f"  skipped: {s}")
        for s in survived:
            label, why = why_survived.get(s, ("SURVIVED", "survives the full suite"))
            print(f"  {label}: {s} -- {why}")
    # A SKIP IS NOT A PASS. This returned 0 whenever nothing survived, so a mutation whose target had
    # drifted, or whose tests were already red, was reported on one line and then counted as if it had been
    # evaluated -- and the process exit code, which is what CI reads, said everything was fine. Measured
    # today: `74/75 killed, 0 survived, 1 skipped` exited 0, and the 75th mutation was never run. That is
    # the shape this repository keeps finding -- a check that cannot report the thing it looks for is
    # indistinguishable from a clean result -- sitting in the tool whose whole job is to catch it.
    return 1 if (survived or skipped) else 0


def _shown(path):
    # A spec on another drive than the checkout (Windows: C: and D:) is not a crash (2026-09-28).
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", nargs="?", default=os.path.join("tools", "mutations.json"),
                    help="JSON list of {name, file, old, new, tests}")
    args = ap.parse_args()

    path = args.spec if os.path.isabs(args.spec) else os.path.join(ROOT, args.spec)
    with open(path, encoding="utf-8") as fh:
        mutations = json.load(fh)
    if not mutations:
        print("the spec is empty: a run over zero mutations is a green result over nothing")
        return 1
    mutations, elsewhere = split_by_platform(mutations)
    print(f"{len(mutations)} mutations from {_shown(path)}"
          + (f", {len(elsewhere)} more for another platform" if elsewhere else "") + "\n")
    report_elsewhere(elsewhere)
    if not mutations:
        print("every entry names another platform: nothing runs here")
        return 0
    return run(mutations)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    __import__("_key_home").isolate("mutation_check")    # heads and keys out of the real key home
    raise SystemExit(main())
