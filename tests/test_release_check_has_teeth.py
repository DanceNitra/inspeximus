"""A release gate that has never been seen to fail is not a gate.

`tools/release_check.py` exists because three releases in a row shipped a stale version string that a
human was supposed to remember (1.86.0, 1.89.0, and `CITATION.cff` across 111 released versions). Every
one of those was a check that COULD have existed and did not.

So each check here is exercised in BOTH directions on a temp tree: it passes on a consistent copy of
the real repository, and it fails on a copy with exactly one thing wrong. The pairing is the point --
a check verified only in the passing direction cannot distinguish "the tree is clean" from "the reader
stopped matching", which is the failure mode that produced the 111-release drift in the first place.

Every case that CORRUPTS something works on a COPY, never on the working tree. Three cases run against
the real repository on purpose -- the two audit legs and the probe-receipt guard -- because their
subject is the real audits and the real probe writer; each of those restores what it touched in a
`finally`, and the probe one carries a control that fails if the writer stops writing.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import release_check  # noqa: E402
import release_notes  # noqa: E402


# ── a minimal but REAL tree ─────────────────────────────────────────────────────────────────────────
CARRIER_FILES = ("pyproject.toml", "CITATION.cff", "server.json", "glama.json",
                 "docs/DEEP_DIVE.md", "CHANGELOG.md")


def _tree(tmp_path):
    """Copy every file the checklist reads. Real content, so a defect has to be introduced on purpose."""
    for rel in CARRIER_FILES:
        # Carriers carry a PATH now, not just a name: the prose version moved into docs/ when the README
        # became a landing page. Copying by basename would have put DEEP_DIVE.md at the root, where the
        # checker does not look -- a tree that is "missing" a carrier it was just handed.
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(ROOT, rel), dest)
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), tmp_path / ".claude-plugin")
    (tmp_path / "inspeximus").mkdir()
    shutil.copy(os.path.join(ROOT, "inspeximus", "core.py"), tmp_path / "inspeximus" / "core.py")
    # The notes template belongs to the tree under test. It used to be read from the real repository
    # regardless of `root`, so a temp tree silently borrowed it and the notes were assembled from two
    # different checkouts at once; `template_for()` now refuses that, which is what made this line
    # necessary.
    (tmp_path / "docs").mkdir(exist_ok=True)   # a carrier lives in docs/ now, so it may already exist
    shutil.copy(os.path.join(ROOT, "docs", "RELEASE_NOTES_TEMPLATE.md"),
                tmp_path / "docs" / "RELEASE_NOTES_TEMPLATE.md")
    return tmp_path


class _Rep(release_check.Report):
    """The real Report, silenced, so the assertions read the status the tool actually recorded."""

    def add(self, name, status, detail):
        self.rows.append((name, status, detail))

    def status(self, name):
        return [row[1] for row in self.rows if row[0] == name][0]

    def detail(self, name):
        return [row[2] for row in self.rows if row[0] == name][0]


# ── version carriers: both directions, one file at a time ───────────────────────────────────────────
def test_the_carrier_check_passes_on_a_consistent_tree(tmp_path):
    """THE CONTROL. Without it, every failure below could be the copy being broken rather than the
    defect being caught."""
    rep = _Rep()
    release_check.check_version_carriers(rep, _tree(tmp_path))
    assert rep.status("version carriers") == release_check.PASS, rep.detail("version carriers")


@pytest.mark.parametrize("rel,mangle", [
    # Exactly the mistake each release actually made, one at a time.
    ("CITATION.cff", lambda t: re.sub(r'^version:.*$', "version: 1.1.0", t, count=1, flags=re.M)),
    ("inspeximus/core.py", lambda t: t.replace('__version__ = "', '__version__ = "9.9.9+', 1)),
    ("docs/DEEP_DIVE.md", lambda t: re.sub(r'(?<![\w.])v\d+\.\d+\.\d+(?![\w.])', "v1.85.0", t, count=1)),
    ("server.json", lambda t: t.replace('"version": "', '"version": "0.0.0-', 1)),
    (".claude-plugin/plugin.json", lambda t: t.replace('"version": "', '"version": "0.0.0-', 1)),
])
def test_the_carrier_check_fails_when_one_file_disagrees(tmp_path, rel, mangle):
    root = _tree(tmp_path)
    p = root / rel
    p.write_text(mangle(p.read_text(encoding="utf-8")), encoding="utf-8")
    rep = _Rep()
    release_check.check_version_carriers(rep, root)
    assert rep.status("version carriers") == release_check.FAIL, \
        "%s was left disagreeing with pyproject and the checklist passed" % rel
    assert rel.split("/")[-1] in rep.detail("version carriers"), \
        "the failure must NAME the file, or the next person has to hunt for it"


def test_a_missing_carrier_fails_rather_than_being_skipped(tmp_path):
    """Not being able to look is not the same as having looked. A deleted manifest is the strongest
    version of "the reader found nothing", and it must be loud."""
    root = _tree(tmp_path)
    (root / "CITATION.cff").unlink()
    rep = _Rep()
    release_check.check_version_carriers(rep, root)
    assert rep.status("version carriers") == release_check.FAIL
    assert "missing" in rep.detail("version carriers")


def test_a_carrier_whose_version_key_vanished_fails(tmp_path):
    """The subtle one, and the reason this file exists. If `version:` is renamed, a regex-based reader
    matches nothing -- and "no version found" reads exactly like "every version agrees" unless the
    empty result is treated as a defect. It is."""
    root = _tree(tmp_path)
    p = root / "CITATION.cff"
    p.write_text(re.sub(r'^version:', "release:", p.read_text(encoding="utf-8"), count=1, flags=re.M),
                 encoding="utf-8")
    rep = _Rep()
    release_check.check_version_carriers(rep, root)
    assert rep.status("version carriers") == release_check.FAIL
    assert "declares no version" in rep.detail("version carriers")


def test_the_checklist_covers_every_file_the_pinner_writes(tmp_path):
    """The two tools must not drift apart.

    The pinner WRITES the version into a set of files; the checklist READS it back out of a set of
    files. If tomorrow's pinner learns a new manifest and the checklist does not, the release gate
    goes quiet about exactly the file most likely to be wrong -- which is the whole history of this
    repository's version bugs.

    So this does not compare two lists. It runs the real pinner against a copy with a wrong version,
    observes which files it CHANGED, and requires each of them to be a carrier the checklist checks.
    A filename string in either source proves nothing; a changed file is an outcome.
    """
    import importlib.util
    root = _tree(tmp_path)
    before = {rel: (root / rel).read_bytes()
              for rel in list(CARRIER_FILES) + [".claude-plugin/plugin.json",
                                                ".claude-plugin/marketplace.json",
                                                "inspeximus/core.py"]}
    spec = importlib.util.spec_from_file_location(
        "pinner_coverage", os.path.join(ROOT, "packages", "_pin_server_json.py"))
    pinner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pinner)
    pinner.ROOT = root
    assert pinner.main(["_", "99.98.97"]) == 0

    changed = {rel for rel, blob in before.items() if (root / rel).read_bytes() != blob}
    assert changed, "the pinner changed nothing, so this test compared nothing"
    uncovered = changed - set(release_check.REQUIRED_CARRIERS)
    assert not uncovered, \
        "the pinner writes %s, which the release checklist does not verify" % sorted(uncovered)


# ── the changelog ───────────────────────────────────────────────────────────────────────────────────
def test_the_changelog_check_passes_on_the_real_changelog(tmp_path):
    rep = _Rep()
    release_check.check_changelog(rep, _tree(tmp_path))
    assert rep.status("changelog entry") == release_check.PASS, rep.detail("changelog entry")


def test_a_release_with_no_changelog_entry_fails(tmp_path):
    root = _tree(tmp_path)
    p = root / "pyproject.toml"
    p.write_text(re.sub(r'^version = ".*"$', 'version = "99.98.97"',
                        p.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
    rep = _Rep()
    release_check.check_changelog(rep, root)
    assert rep.status("changelog entry") == release_check.FAIL
    assert "no `## 99.98.97` entry" in rep.detail("changelog entry")


def test_an_entry_that_is_not_the_newest_fails(tmp_path):
    """A version whose entry exists but sits below a newer one is a release cut from the wrong commit."""
    root = _tree(tmp_path)
    older = release_check.VERSION_HEADING.findall(
        (root / "CHANGELOG.md").read_text(encoding="utf-8"))[3]
    p = root / "pyproject.toml"
    p.write_text(re.sub(r'^version = ".*"$', 'version = "%s"' % older,
                        p.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
    rep = _Rep()
    release_check.check_changelog(rep, root)
    assert rep.status("changelog entry") == release_check.FAIL
    assert "newest goes on top" in rep.detail("changelog entry")


# ── the zero-dependency guard ───────────────────────────────────────────────────────────────────────
def test_a_declared_dependency_breaks_the_zero_dependency_check(tmp_path):
    """The claim on the first line of the README. `claims_audit.py` checks the same thing from the
    other side; this is the release-time leg, and it fails on the declaration a user's installer obeys."""
    root = _tree(tmp_path)
    p = root / "pyproject.toml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "[project.optional-dependencies]", 'dependencies = ["requests>=2"]\n\n[project.optional-dependencies]',
        1), encoding="utf-8")
    rep = _Rep()
    release_check.check_zero_dependencies(rep, root)
    assert rep.status("zero dependencies") == release_check.FAIL
    assert "requests" in rep.detail("zero dependencies")


def test_the_import_blocker_really_blocks(tmp_path):
    """THE CONTROL INSIDE THE CONTROL. The runtime leg proves `import inspeximus` needs nothing
    third-party -- but only if the blocker blocks. Aimed at a module verified to import normally
    first, so "nothing was blocked" cannot be confused with "nothing was importable"."""
    normal = subprocess.run([sys.executable, "-c", "import pytest"], cwd=ROOT,
                            capture_output=True, text=True)
    assert normal.returncode == 0, "pytest must import normally for this control to mean anything"
    blocked = release_check._blocked_run(ROOT, "import pytest")
    assert blocked.returncode != 0 and "THIRD_PARTY_BLOCKED" in blocked.stderr


def test_the_blocker_lets_the_stdlib_through_on_whatever_python_is_running():
    """It must PASS the stdlib as surely as it blocks site-packages, or the runtime leg fails for the
    wrong reason and reads as a dependency that is not there.

    This is the pair to the CI failure that produced the current implementation. The first version
    keyed on `sys.stdlib_module_names`, which is 3.10+, while `pyproject.toml` declares `>=3.8` and CI
    runs 3.9 -- so on the OLDEST supported Python the blocker could not be built, the leg degraded to a
    SKIP, and the guard on the README's first-line claim was absent exactly where it matters most.
    Every local run here is 3.12, so only the 3.9 leg could see it. Deciding by LOCATION
    (site-packages / dist-packages / .egg) needs no version table at all.
    """
    proc = release_check._blocked_run(
        ROOT, "import json, re, sqlite3, hashlib, dataclasses; print('STDLIB OK')")
    assert proc.returncode == 0 and "STDLIB OK" in proc.stdout, proc.stderr[-400:]
    assert "stdlib_module_names" not in release_check._BLOCKER, \
        "the blocker must not depend on a 3.10+ API; this package supports 3.8+"


# ── the audits leg: it CALLS the existing audits, so the test is that the call is honest ────────────
def test_the_audits_leg_passes_against_the_real_audits():
    """THE CONTROL for the two below."""
    rep = _Rep()
    release_check.check_audits(rep, __import__("pathlib").Path(ROOT))
    assert rep.status("audits") == release_check.PASS, rep.detail("audits")


def test_a_missing_audit_script_fails_rather_than_being_skipped(tmp_path):
    """`release.yml` refuses to publish without these. A local checklist that shrugs when one is
    absent would clear a release the workflow is about to block."""
    rep = _Rep()
    release_check.check_audits(rep, tmp_path)
    assert rep.status("audits") == release_check.FAIL
    assert "claims_audit.py is missing" in rep.detail("audits")


def test_the_run_leaves_no_probe_churn_in_the_working_tree():
    """A checklist you run immediately before `git commit && git tag` must not dirty the tree.

    `probes/governance_sufficiency_probe.py` regenerates `probes/governance_sufficiency_bytes.json`
    with fresh record ids and Ed25519 keys -- 45 lines of churn in a TRACKED file, one `git add -A`
    from being committed into the release. The suite executes it, which is why the checklist snapshots
    around the whole run rather than around one leg. (My first attribution here was the audits, on the
    evidence that the file was dirty right after an audits run; running each audit alone left it
    byte-identical. Measuring which step writes is not the same as noticing which step preceded it.)

    Both directions, with the REAL writer:
      1. THE FIXTURE CONTROL -- run the probe and require the receipt to change. If it stops changing,
         the guarantee below is untested and this says so instead of going quietly green.
      2. Snapshot, run the probe, restore -- and require the bytes back.
    """
    import pathlib
    root = pathlib.Path(ROOT)
    receipt = root / "probes" / "governance_sufficiency_bytes.json"
    probe = root / "probes" / "governance_sufficiency_probe.py"
    original = receipt.read_bytes()
    try:
        assert subprocess.run([sys.executable, str(probe)], cwd=ROOT,
                              capture_output=True, text=True).returncode == 0
        assert receipt.read_bytes() != original, \
            "the probe no longer rewrites its receipt; this fixture has nothing to restore and the " \
            "guarantee below is measuring nothing"
        receipt.write_bytes(original)

        snapshot = release_check._probe_snapshot(root)
        subprocess.run([sys.executable, str(probe)], cwd=ROOT, capture_output=True, text=True)
        assert receipt.name in release_check.restore_probe_snapshot(snapshot)
        assert receipt.read_bytes() == original, "the snapshot did not restore the receipt"
    finally:
        receipt.write_bytes(original)


def test_the_restore_is_byte_exact_even_on_a_receipt_that_is_not_utf8(tmp_path):
    """Bytes, not text. The first version of this snapshot read in text mode and crashed the whole
    checklist: `governance_sufficiency_bytes.json` carries a 0x97 at offset 2321 and is not valid
    UTF-8. Text mode also rewrites LF as CRLF on Windows, which leaves a file permanently dirty with
    identical content -- the bug `tools/mutation_check.py` documents at length."""
    (tmp_path / "probes").mkdir()
    p = tmp_path / "probes" / "receipt.json"
    p.write_bytes(b'{"a": 1}\n{"b": "\x97"}\n')          # invalid UTF-8, LF endings
    snapshot = release_check._probe_snapshot(tmp_path)
    assert snapshot, "the snapshot found no receipts, so nothing below is being tested"

    p.write_bytes(b'{"a": 2}\r\n')                        # what a run would leave behind
    assert release_check.restore_probe_snapshot(snapshot) == ["receipt.json"]
    assert p.read_bytes() == b'{"a": 1}\n{"b": "\x97"}\n'
    # and a second restore is a no-op: an unchanged file is not rewritten at all
    assert release_check.restore_probe_snapshot(snapshot) == []


def test_an_audit_that_cannot_fail_is_reported_as_a_failure(tmp_path):
    """FALSIFICATION, and the one that matters.

    `GOV_FALSIFY=1` injects a defect the governance audit must detect. Here both audits are replaced
    by scripts that exit 0 unconditionally -- the shape of an audit that has stopped examining its
    target -- so the injected defect goes unnoticed. A green from those is worth nothing, and the
    checklist must say so instead of reporting the green.
    """
    for name in ("claims_audit.py", "governance_audit.py"):
        (tmp_path / name).write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    rep = _Rep()
    release_check.check_audits(rep, tmp_path)
    assert rep.status("audits") == release_check.FAIL
    assert "GOV_FALSIFY" in rep.detail("audits")


# ── the release notes ───────────────────────────────────────────────────────────────────────────────
def test_the_notes_build_for_the_current_version():
    notes = release_notes.render()
    for section in release_notes.REQUIRED_SECTIONS:
        assert section in notes, section
    assert not release_notes.verify(notes, release_notes.pyproject_version())


def test_the_example_in_the_notes_actually_runs_and_prints_what_it_promises():
    """The one claim in the notes a reader will test within thirty seconds of arriving."""
    notes = release_notes.render()
    code, expected = release_notes.python_example(notes)
    assert code and expected
    assert not [p for p in release_notes.verify(notes, release_notes.pyproject_version())
                if "example" in p]


def _mutate_example(notes, old, new):
    """Apply a mutation INSIDE the fenced example only, and prove it landed.

    Both controls below used to hardcode a literal from one release's example -- `m.remember(` and
    `# -> ['The staging database is db-7.internal.']`. That is a mutation aimed at a moving target: the
    next release writes a different example, `str.replace` finds nothing, the notes go through
    unmutated, and the control then asserts that an UNBROKEN example produces a problem. It cannot pass
    once the example changes, which is the good half; the bad half is that it says nothing about the
    gate, only about which release happened to be on top. Deriving the target from the example that is
    actually there fixes both, and the assertion here is what stops it silently degrading again.
    """
    code, _ = release_notes.python_example(notes)
    assert code, "the notes carry no python example, so there is nothing for this control to break"
    mutated_code = code.replace(old, new, 1)
    assert mutated_code != code, \
        f"the mutation {old!r} did not change the example, so this control is aimed at nothing:\n{code}"
    return notes.replace(code, mutated_code, 1)


def test_a_broken_example_is_caught():
    """FALSIFICATION. Break what the example promises to print and the gate must go red -- otherwise
    the check above is only asserting that a subprocess exited 0."""
    notes = release_notes.render()
    _, expected = release_notes.python_example(notes)
    assert expected, "the example states no expected output; this control would test nothing"
    notes = _mutate_example(notes, "# -> %s" % expected, "# -> whatever we wish it said")
    problems = release_notes.verify(notes, release_notes.pyproject_version())
    assert any("the notes promise" in p for p in problems), problems


def test_an_example_that_raises_is_caught():
    notes = release_notes.render()
    code, _ = release_notes.python_example(notes)
    call = re.search(r'^\s*(\w+)\.(\w+)\(', code or "", re.M)
    assert call, f"the example makes no method call to break; this control would test nothing:\n{code}"
    notes = _mutate_example(notes, "%s.%s(" % call.groups(), "%s.no_such_method(" % call.group(1))
    problems = release_notes.verify(notes, release_notes.pyproject_version())
    assert any("does not run" in p for p in problems), problems


@pytest.mark.parametrize("phrase", [
    "This is a revolutionary rewrite of the write path.",
    "Recall is now blazing fast.",
    "No other library ships verifiable erasure.",
    "Competitors cant do this.",
    "A seamless upgrade.",
])
def test_unverifiable_claim_language_is_rejected(phrase):
    """A claim with no procedure behind it, or a claim about somebody else's product we never measured.
    Both are things a reader cannot check, which is the one rule the template enforces."""
    notes = release_notes.render().replace("## What changed", "## What changed\n\n" + phrase, 1)
    problems = release_notes.verify(notes, release_notes.pyproject_version())
    assert any("unverifiable-claim language" in p for p in problems), (phrase, problems)


def test_the_language_lint_is_silent_on_the_entire_real_changelog():
    """The other half of that measurement. A lint that fires on ordinary technical prose gets switched
    off, and a gate that is switched off is not a gate -- so this pins the false-positive count at
    zero across every entry we have ever written."""
    text = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    hits = [(pat, m) for pat in release_notes.INFLATION
            for m in re.findall(pat, text, re.I)]
    assert not hits, hits[:5]


@pytest.mark.parametrize("version", ["1.73.0", "1.70.0"])
def test_a_marker_in_the_HEADING_ONLY_is_still_surfaced(version):
    """The defect measuring found in this generator, kept as a fixture so it cannot come back.

    `what_breaks` read the entry BODY. Measured across CHANGELOG.md, 2 of the 9 entries that declare a
    behaviour change -- 1.73.0 and 1.70.0 -- carry `(BEHAVIOUR CHANGE)` in the HEADING and nowhere
    else, so on exactly those releases the section reported "no marker" about an entry whose title
    said otherwise. A check that cannot see its target reports safe.
    """
    heading, body = release_notes.entry_for(version)
    assert release_notes.MARKER.search(heading) and not release_notes.MARKER.search(body), \
        "%s no longer reproduces the heading-only case; this fixture is measuring nothing" % version
    out = release_notes.what_breaks(body, version, release_notes.ROOT, heading)
    assert "BEHAVIOUR CHANGE" in out and "No line in" not in out


def test_a_marker_in_the_body_is_surfaced():
    """The sibling case, so the fix above cannot have traded one blind spot for the other."""
    heading, body = release_notes.entry_for("1.85.0")
    out = release_notes.what_breaks(body, "1.85.0", release_notes.ROOT, heading)
    assert "BEHAVIOUR CHANGE" in out and "No line in" not in out


def test_an_unmarked_release_says_so_rather_than_promising_compatibility():
    heading, body = release_notes.entry_for("1.89.0")
    out = release_notes.what_breaks(body, "1.89.0", release_notes.ROOT, heading)
    assert "No line in the 1.89.0 changelog entry" in out
    assert "nothing breaks" not in out.lower(), \
        "the section must describe the ENTRY, never promise something about the code"


def test_a_major_bump_with_no_marker_is_a_todo_the_gate_rejects(tmp_path):
    """Arithmetic, so it has no false positives: this project's own scheme says MAJOR means breaking.
    A major bump whose entry declares nothing is a contradiction the author has to resolve.

    The previous version is read from the changelog, so this needs a tree with one -- passing a
    version the changelog has never heard of would make `bump_class` return None and the test would
    have proved only that an unknown version is not a major bump.
    """
    assert release_notes.bump_class("2.0.0", "1.89.0") == "major"
    assert release_notes.bump_class("1.90.0", "1.89.0") == "minor"
    assert release_notes.bump_class("1.89.1", "1.89.0") == "patch"
    assert release_notes.bump_class("1.0.0", None) is None

    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 2.0.0 - the big one\n\nbody\n\n## 1.89.0 - before\n\nbody\n", encoding="utf-8")
    out = release_notes.what_breaks("body with no marker at all", "2.0.0", tmp_path, "## 2.0.0 - x")
    assert out.startswith("TODO(what breaks)"), out
    # and the TODO is what the gate rejects, not just a string in the output
    notes = "## Who should upgrade\n## What changed\n## What breaks\n%s\n## Try it\n## Check it yourself" % out
    assert any("unfilled" in p for p in release_notes.verify(notes, "2.0.0", tmp_path))


def test_prose_that_merely_sounds_like_a_break_is_not_guessed_at():
    """Deliberate non-feature, with the measurement behind it. "no longer" appears in 17% of entries
    and "removed" in 9%, almost always describing a fix. A section that demanded an answer on a
    quarter of releases would be switched off, and a gate that is switched off is not a gate."""
    body = "unresolvable parents are no longer dropped silently; the dead branch was removed"
    out = release_notes.what_breaks(body, "1.89.1", release_notes.ROOT, "## 1.89.1 - x")
    assert out.startswith("No line in")


@pytest.mark.parametrize("version,audience", [
    ("1.89.0", "YOU USE `slash()`/`restore()`"),
    ("1.88.1", "YOU USE THE MCP SERVER OR THE LANGGRAPH STORE"),
    ("1.88.0", "YOU RELY ON ERASURE"),
    ("1.87.0", "YOU RELY ON ERASURE"),
])
def test_the_audience_clause_stops_at_the_colon(version, audience):
    """The second defect measuring found here: the split carried a lookbehind requiring a lowercase
    letter, `)` or backtick before the colon, so `UPGRADE IF YOU RELY ON ERASURE: 1.86.0 deletes the
    wrong person's records` did not split at all and the audience line swallowed the whole headline.
    These are every entry in CHANGELOG.md that uses the convention, so the fixture is the corpus."""
    heading, _ = release_notes.entry_for(version)
    out = release_notes.who_should_upgrade(heading, version)
    assert out == "Upgrade if this is true of you: **%s**." % audience, out


def test_the_template_comes_from_the_tree_being_released(tmp_path):
    """It used to be a module constant pinned to the script's own repository, so `--root OTHER` read
    the changelog from OTHER and the template from here -- notes assembled from two checkouts, with
    nothing in the output to show it. Both directions: a tree WITH its own template must use that one,
    and a tree WITHOUT one must fail loudly instead of silently borrowing ours."""
    root = _tree(tmp_path)
    tmpl = root / "docs" / "RELEASE_NOTES_TEMPLATE.md"
    tmpl.write_text(tmpl.read_text(encoding="utf-8").replace(
        "# inspeximus {{VERSION}}", "# inspeximus {{VERSION}} (from the temp tree)"), encoding="utf-8")
    assert "(from the temp tree)" in release_notes.render(None, root)
    assert "(from the temp tree)" not in release_notes.render()      # the real repo is untouched

    tmpl.unlink()
    with pytest.raises(SystemExit):
        release_notes.render(None, root)


def test_the_notes_refuse_to_invent_an_audience(tmp_path):
    """`Who should upgrade` is DERIVED from the heading. When the heading does not say, the generator
    emits a TODO and `--check` fails, rather than writing "everyone should upgrade" -- which would be
    the one kind of sentence this whole template exists to keep out."""
    root = _tree(tmp_path)
    version = release_notes.pyproject_version(root)
    p = root / "CHANGELOG.md"
    text = p.read_text(encoding="utf-8")
    heading = re.search(r'^## %s.*$' % re.escape(version), text, re.M).group(0)
    p.write_text(text.replace(heading, "## %s - some things happened" % version, 1), encoding="utf-8")
    notes = release_notes.render(version, root)
    assert "TODO(who should upgrade)" in notes
    assert any("unfilled" in prob for prob in release_notes.verify(notes, version, root))


def test_release_notes_check_exits_non_zero_on_a_defect(tmp_path):
    """End to end through the CLI, because the exit code is what the release gate reads."""
    root = _tree(tmp_path)
    (root / "tools").mkdir()
    shutil.copy(os.path.join(ROOT, "tools", "release_notes.py"), root / "tools" / "release_notes.py")
    tmpl = root / "docs" / "RELEASE_NOTES_TEMPLATE.md"
    tmpl.write_text(tmpl.read_text(encoding="utf-8").replace("## What breaks", "## Nothing breaks"),
                    encoding="utf-8")
    proc = subprocess.run([sys.executable, str(root / "tools" / "release_notes.py"),
                           "--check", "--root", str(root)], capture_output=True, text=True)
    assert proc.returncode != 0, proc.stdout
    assert "missing section" in proc.stdout


# ── the whole checklist, through its exit code ──────────────────────────────────────────────────────
def test_the_checklist_exit_code_distinguishes_pass_fail_and_skip():
    """0 / 1 / 2 are the contract RELEASING.md relies on. An unrun check must not read as a passing one:
    that equivalence is how a green suite came to mean "the case never arises"."""
    rep = release_check.Report()
    rep.rows = [("a", release_check.PASS, "")]
    assert rep.exit_code() == 0
    rep.rows.append(("b", release_check.SKIP, ""))
    assert rep.exit_code() == 2
    rep.rows.append(("c", release_check.FAIL, ""))
    assert rep.exit_code() == 1


def test_skipping_the_suite_can_never_exit_zero(tmp_path):
    rep = _Rep()
    release_check.check_tests(rep, tmp_path, skip=True)
    assert rep.status("test suite") == release_check.SKIP
    assert rep.exit_code() == 2


def _init_repo(tmp_path):
    """A real git checkout with one tracked file, so the fingerprint has something to read."""
    d = tmp_path / "tree"
    d.mkdir()
    (d / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (d / "probes").mkdir()
    (d / "probes" / "r.result.json").write_text("{}", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for a in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(("git",) + a, cwd=str(d), env=env, capture_output=True, text=True)
    return d


def test_the_tree_fingerprint_sees_a_change_and_ignores_our_own_receipts(tmp_path):
    """The 2.5.0 run began before an MCP commit landed and ended after it: the `mcp server imports` leg
    PASSED on 67 tools while the suite -- 23 minutes later -- FAILED against 68, and the report read as
    one verdict about one tree. The guard added for that has to fire on a real edit, and must NOT fire
    on the probe receipts this gate rewrites itself, or it would refuse every run it performs correctly.
    Both directions, because a guard exercised only one way cannot tell "quiescent" from "not looking"."""
    d = _init_repo(tmp_path)
    base = release_check._tree_fingerprint(d)
    assert base is not None, "a real checkout must fingerprint; None here would mean the guard is off"

    (d / "probes" / "r.result.json").write_text('{"n": 1}', encoding="utf-8")
    assert release_check._tree_fingerprint(d) == base, (
        "our own probe receipts must NOT trip the guard -- this gate rewrites them by design")

    (d / "kept.py").write_text("x = 2\n", encoding="utf-8")
    assert release_check._tree_fingerprint(d) != base, (
        "a tracked source edit mid-run MUST trip the guard -- that is the whole point")


def test_the_fingerprint_says_None_rather_than_guessing_outside_a_checkout(tmp_path):
    """Not-a-checkout is UNKNOWN, not clean. An empty fingerprint would compare equal to itself and
    report a quiescent tree for a tree it never managed to read."""
    plain = tmp_path / "nogit"
    plain.mkdir()
    assert release_check._tree_fingerprint(plain) is None


def test_the_error_message_names_a_remedy_THAT_ACTUALLY_WORKS():
    """The message offered two ways out and the code implemented one.

    `who_should_upgrade` told an author to "name the users affected, or say plainly that it affects
    nobody's code", while only `UPGRADE IF/WHEN` was ever accepted. A release that genuinely changes
    nothing observable therefore had no honest way to pass: the only route through was to invent an
    audience for it. An error message that names a fix the code rejects sends the reader in a circle.

    Both arms are asserted here, because implementing the second remedy without testing it is how the
    first one came to be wrong.
    """
    assert "TODO" not in release_notes.who_should_upgrade(
        "AFFECTS NOBODY'S CODE: a test was passing for the wrong reason", "9.9.9")
    assert "TODO" not in release_notes.who_should_upgrade(
        "UPGRADE IF YOU BACK-DATE FACTS: valid_from took only a float", "9.9.9")
    # CONTROL: a heading that states no audience at all must still be refused, or this gate stops
    # gating and every future release passes by saying nothing.
    todo = release_notes.who_should_upgrade("a heading that names nobody", "9.9.9")
    assert todo.startswith("TODO"), todo
    # and the remedy the refusal names must be one of the two that work
    assert "AFFECTS NOBODY'S CODE" in todo
