"""Every number we publish must have a command that reproduces it, or it does not get published.

This is the guard, not the audit. The audit (2026-08-01) found the defect once; this file is what stops
it coming back, and the shape it defends against is specific. Our own CHANGELOG carried the headline
retrieval pair marked "reported, not independently reproducible from this repo" — a number a reader
cannot re-run is not evidence. The same audit then found that this was a CLASS, not an instance:

  * 31 receipt paths in README/docs/CHANGELOG pointed at `inspeximus/probes/...` while the probes live
    at `probes/...`. A CHANGELOG entry had already "fixed" two of them and stopped there.
  * five internal README anchors pointed at sections that had been moved out of the file, including the
    one advertising "the measured integrity number below" — the number was no longer on the page.
  * the MCP tool count was published as 30 (MCP_LISTINGS.md), 15 and 56 (the homepage) at the same time,
    while the server registered 56. Three surfaces, one truth, no error anywhere.
  * "measured 15/15 on a verified-forgetting severe-test" and "severe-test 8/8" had no producing
    artifact at all, and a whole README section documented two files not present in the repository.

None of that was caught by anything, because nothing read the numbers. So:

  1. every numeric token a reader sees on README.md / MCP_LISTINGS.md / index.html must be registered,
     either as a claim with a reproduction command and a status, or as a declared non-claim with a
     reason AND an exact expected count;
  2. every claim's `pin` sentence must still exist, so a registry row cannot outlive what it audits;
  3. every reproduction command must name a file that exists;
  4. self-referential figures (the MCP tool count, the example audit summary) are read from the code.

THE CONTROLS ARE THE POINT. A guard nobody has watched fail has not been tested, so the positive
controls below inject a bogus number, a moved sentence, an extra occurrence and a deleted probe into a
COPY of the tree and assert the audit catches each one. If a control ever goes green, the guard has
stopped measuring and this file has to be fixed before its passes mean anything again.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claims_audit as ca  # noqa: E402


@pytest.fixture
def sandbox(tmp_path):
    """A copy of the surface + the pieces the audit reads, so a control can mutate it safely.

    Copying rather than editing in place matters: a control that edited README.md would leave the repo
    dirty if the assertion failed, and the next run would then be measuring the damage from the last one.
    """
    dst = tmp_path / "repo"
    dst.mkdir()
    for name in ca.SURFACE:
        # SURFACE entries carry a path, not just a filename. This used to assume flat names, and the day
        # the long-form README moved to docs/DEEP_DIVE.md the copy raised for a missing parent -- which
        # ERRORED thirteen controls at setup rather than failing them. An erroring control still reads as
        # "not a failure" in a summary line, so make the parent instead of assuming it.
        (dst / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dst / name)
        assert (dst / name).exists(), f"{name} did not reach the sandbox; every control over it is vacuous"
    (dst / "inspeximus").mkdir()
    shutil.copy2(ROOT / "inspeximus" / "mcp_server.py", dst / "inspeximus" / "mcp_server.py")
    for sub in ("probes", "docs", "tools", "benchmarks"):
        (dst / sub).mkdir(exist_ok=True)   # docs/ now already exists: a SURFACE file lives in it
    # Everything a reproduction command can name has to exist here, or BROKEN-COMMAND fires on an
    # untouched sandbox and every control that asserts "clean before, dirty after" measures nothing.
    for src, pat in ((ROOT / "probes", "*.py"), (ROOT / "tools", "*.py")):
        for p in src.glob(pat):
            (dst / src.name / p.name).touch()
    # DERIVED, not listed. This used to name `benchmarks/locomo/run.py` by hand, and the moment a new
    # claim registered `benchmarks/chain_binding/probe.py` the untouched sandbox came up dirty with
    # BROKEN-COMMAND -- so the two controls below asserted "clean before, dirty after" against a
    # sandbox that was never clean, and measured nothing. A hardcoded list of the things a registry
    # can name is a second place to forget; read the registry instead.
    for _c in ca.NUMBER_CLAIMS:
        for _tok in ca.ARTIFACT_PATH.findall(_c["command"]):
            (dst / _tok).parent.mkdir(parents=True, exist_ok=True)
            (dst / _tok).touch()
    (dst / "docs" / "CLAIMS.md").touch()
    shutil.copy2(ROOT / "docs" / "integration_conformance.json", dst / "docs" / "integration_conformance.json")
    return dst


def _live_tool_count():
    """Read it, never type it — a control hardcoding 56 goes stale the moment the server grows."""
    return len(re.findall(r"@mcp\.tool\(\)",
                          (ROOT / "inspeximus" / "mcp_server.py").read_text(encoding="utf-8")))


def _kinds(root):
    return {kind for kind, _where, _msg in ca.audit_numbers(root)[0]}


# ── the guard itself ────────────────────────────────────────────────────────────────────────────────

def test_every_published_number_is_registered():
    """The headline assertion: no number on the reader-facing surface is unaccounted for."""
    problems, stats = ca.audit_numbers(ROOT)
    assert not problems, "\n".join(f"[{k}] {w}: {m}" for k, w, m in problems)
    assert stats["published"] > 100, "the scanner found almost nothing; it is probably masking too much"
    assert stats["claims"] > 0


def test_the_ratio_is_reported_and_not_flattering():
    """The measurement this unit owes: how many published numbers a reader can actually reproduce.

    Asserted as a floor rather than an equality so the test does not have to be edited every time a
    claim is added, and asserted at all so the ratio cannot silently collapse to zero.
    """
    repro = [c for c in ca.NUMBER_CLAIMS if c["status"].startswith("REPRODUCIBLE")]
    assert len(repro) >= 20, "fewer reproducible claims than the audit landed with; something was removed"
    assert len(repro) < len(ca.NUMBER_CLAIMS), (
        "every single claim is marked reproducible. That has never been true of this surface, and a "
        "registry with no EXTERNAL/PENDING rows is far more likely to be dishonest than complete."
    )


def test_every_claim_status_is_one_of_the_declared_five():
    for c in ca.NUMBER_CLAIMS:
        assert c["status"] in ca.STATUSES, f"{c['id']} has an invented status {c['status']!r}"


def test_every_reproduction_command_names_a_file_that_exists():
    """Assert the target RESOLVES. A command pointing at a deleted probe is the original defect."""
    named = 0
    for c in ca.NUMBER_CLAIMS:
        for path in ca.ARTIFACT_PATH.findall(c["command"]):
            named += 1
            assert (ROOT / path).exists(), f"claim {c['id']} names {path}, which does not exist"
    assert named >= 10, "no command names a repo path any more; this test would pass on an empty registry"


def test_every_reproducible_claim_actually_carries_a_command():
    for c in ca.NUMBER_CLAIMS:
        if c["status"].startswith("REPRODUCIBLE"):
            assert c["command"].strip(), (
                f"{c['id']} is marked {c['status']} with no command. That is the euphemism this whole "
                f"file exists to prevent."
            )


def test_claims_doc_is_in_sync_with_the_registry():
    """docs/CLAIMS.md is generated. If it drifts, the published table stops describing the checker."""
    doc = ROOT / "docs" / "CLAIMS.md"
    assert doc.exists(), "docs/CLAIMS.md is missing; run `python claims_audit.py --write-claims`"
    on_disk = doc.read_text(encoding="utf-8").replace("\r\n", "\n")
    generated = ca.render_claims_doc(ROOT).replace("\r\n", "\n")
    assert on_disk == generated, "docs/CLAIMS.md is stale — run `python claims_audit.py --write-claims`"


def test_the_claims_doc_states_what_it_does_not_cover():
    """A scope a reader has to infer is a scope that gets over-read.

    CHANGELOG.md and docs/ are NOT token-enforced. If the document ever stops saying so, a green run
    here would read as "every number in the project is backed", which is not what this measures.
    """
    text = (ROOT / "docs" / "CLAIMS.md").read_text(encoding="utf-8")
    assert "not** token-enforced" in text or "not token-enforced" in text
    assert "CHANGELOG.md" in text


def test_no_reader_facing_file_names_an_artifact_that_does_not_exist():
    """The weaker check that covers the surface the token audit does not.

    This is the one that would have caught all 31 `inspeximus/probes/...` paths on the day they were
    written, and it is deliberately broader than SURFACE: a "run this to reproduce" line is a claim
    wherever it appears.
    """
    # Imported, not retyped. This regex used to exist in three copies with three different prefix
    # lists -- and the three agreed with each other while all three were blind to `docs/` and `bench/`.
    pat = ca.ARTIFACT_PATH
    files = [ROOT / "README.md", ROOT / "MCP_LISTINGS.md", ROOT / "index.html",
             ROOT / "probes" / "INTEGRITY_BENCHMARK.md"] + sorted((ROOT / "docs").glob("*.md"))
    missing, checked = [], 0
    for f in files:
        if not f.exists():
            continue
        for path in sorted(set(pat.findall(f.read_text(encoding="utf-8", errors="replace")))):
            checked += 1
            if not (ROOT / path).exists():
                missing.append(f"{f.name} -> {path}")
    assert checked >= 30, (
        f"only {checked} artifact paths were examined; the regex has stopped matching and this check "
        f"is reporting SAFE without having looked"
    )
    assert not missing, "reader-facing files name artifacts that do not exist:\n" + "\n".join(missing)


def test_no_reader_facing_markdown_links_to_a_heading_that_is_gone():
    """Five README anchors pointed at sections that had been moved out, and nothing noticed."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    heads, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            s = re.sub(r"[^\w\s-]", "", m.group(1).strip().lower(), flags=re.U)
            heads.append(s.replace(" ", "-"))
    anchors = sorted(set(re.findall(r"\]\(#([a-z0-9\-]+)\)", text)))
    assert anchors, "no internal anchors found at all; the regex has stopped matching"
    dead = [a for a in anchors if a not in heads]
    assert not dead, f"README links to headings that do not exist: {dead}"


def test_the_cli_entrypoint_exits_non_zero_on_a_problem(sandbox):
    """`python claims_audit.py --numbers` must be usable as a CI gate, exit code and all.

    The first version of this test mutated the sandbox and then ran the subprocess with `cwd=ROOT` --
    and `_repo_root()` resolves from `__file__`, not from the working directory, so the CLI audited the
    real repo and reported clean no matter what the sandbox contained. It asserted `returncode == 0`
    and passed forever, testing nothing. That is exactly the shape this whole file is about, found in
    the file itself. `--root` exists so the REAL entrypoint can be aimed at the mutated copy.

    Exit codes are read directly: `$?` after a pipe is the LAST command's code, which is how a failing
    gate once read as exit 0.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def gate(root):
        return subprocess.run([sys.executable, str(ROOT / "claims_audit.py"), "--numbers", "--root", str(root)],
                              cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    clean = gate(sandbox)
    assert clean.returncode == 0, "the untouched sandbox already fails:\n" + clean.stdout + clean.stderr

    (sandbox / "README.md").write_text(
        (sandbox / "README.md").read_text(encoding="utf-8") + "\n\nWe measured a recall of 0.9914 here.\n",
        encoding="utf-8")
    dirty = gate(sandbox)
    assert dirty.returncode == 1, "the shipped CLI did not fail on an unregistered number:\n" + dirty.stdout
    assert "0.9914" in dirty.stdout, "it failed, but not for the reason we planted"


def test_the_scanner_sees_a_unit_glued_number(sandbox):
    """`--object 90d` was invisible: the trailing lookahead demanded a non-word character.

    The registry's declared count for token "90" therefore agreed with a scanner that could not see
    the second occurrence -- guard and target computed by the same broken rule. This asserts the shape
    is readable now, not that one file happens to contain it.
    """
    # The unit-glued token lives in the long-form document since the README was cut to a landing page.
    doc = sandbox / "docs" / "DEEP_DIVE.md"
    assert "90d" in doc.read_text(encoding="utf-8"), (
        "fixture no longer reproduces: the unit-glued `90d` is gone, so this scanner test would pass "
        "without ever exercising the shape it was written for")
    found = [t for _ln, t, _src in ca.scan_numbers(doc)]
    assert "90" in found
    p = doc
    p.write_text(p.read_text(encoding="utf-8") + "\n\nA latency of 4242ms was observed.\n", encoding="utf-8")
    assert "UNREGISTERED" in _kinds(sandbox), "a unit-glued number is still invisible to the scanner"


def test_the_scanner_sees_a_negative_number(sandbox):
    """`z=-4.79` lost its sign AND its digits to the lookbehind, so the token vanished entirely."""
    p = sandbox / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\nOur forecaster scores z=-4.79 against its marginals.\n",
                 encoding="utf-8")
    problems = ca.audit_numbers(sandbox)[0]
    assert any(k == "UNREGISTERED" and "-4.79" in m for k, _w, m in problems), (
        f"a negative number was published and not flagged; got {problems}")


# ── POSITIVE CONTROLS: the guard must be seen to fail ───────────────────────────────────────────────
# Each control introduces exactly one defect and asserts the audit names it. If any of these stops
# failing, the corresponding check has stopped measuring anything.

def test_CONTROL_a_new_unregistered_number_is_caught(sandbox):
    assert not _kinds(sandbox), "the sandbox is dirty before the control was applied"
    p = sandbox / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\nOn our benchmark inspeximus scores 0.9914.\n",
                 encoding="utf-8")
    assert "UNREGISTERED" in _kinds(sandbox), (
        "a brand-new number was added to README.md and the audit did not notice. The guard is not "
        "reading what a reader reads."
    )


def test_CONTROL_a_second_occurrence_of_a_REGISTERED_token_is_caught(sandbox):
    """The subtler half: reusing a token that is already declared must still fail.

    Without the exact-count rule, "0.75" could be pasted into a new sentence with a new meaning and the
    registry would absorb it silently — a number published with no claim behind it, under cover of a
    row about something else.
    """
    p = sandbox / "index.html"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "</body>", "<p>A completely different figure: 200 records per second.</p></body>"),
        encoding="utf-8")
    assert "COUNT-DRIFT" in _kinds(sandbox)


def test_CONTROL_moving_a_pinned_sentence_is_caught(sandbox):
    """A registry row whose sentence is gone describes nothing, and would keep reporting PASS."""
    p = sandbox / "docs" / "DEEP_DIVE.md"
    before = p.read_text(encoding="utf-8")
    after = before.replace(
        "13 passed · 0 FAILED · 0 skipped · 5 not testable here", "the audit passed")
    assert after != before, "fixture no longer reproduces: the pinned sentence has moved or changed"
    p.write_text(after, encoding="utf-8")
    kinds = _kinds(sandbox)
    assert "STALE-PIN" in kinds and "LIVE-MISMATCH" in kinds


def test_CONTROL_a_command_pointing_at_a_deleted_probe_is_caught(sandbox):
    """The original defect, reproduced: a receipt path that no longer resolves."""
    (sandbox / "probes" / "forget_verification_bench.py").unlink()
    assert "BROKEN-COMMAND" in _kinds(sandbox)


def test_CONTROL_a_published_tool_count_that_disagrees_with_the_server_is_caught(sandbox):
    """The three-surfaces-one-truth defect: MCP_LISTINGS said 30 while the server registered 56.

    The live count is read, not typed. An earlier version of this control hardcoded 56 and broke the
    day the server reached 58 — a guard about stale published counts, itself carrying one.
    """
    live = _live_tool_count()
    p = sandbox / "MCP_LISTINGS.md"
    p.write_text(p.read_text(encoding="utf-8").replace(f"`inspeximus-mcp`, {live} tools",
                                                       "`inspeximus-mcp`, 30 tools"), encoding="utf-8")
    assert "LIVE-MISMATCH" in _kinds(sandbox)


def test_CONTROL_an_adapter_conformance_count_that_disagrees_with_the_ledger_is_caught(sandbox):
    """The homepage advertised nine adapters unqualified while the ledger recorded three broken.

    THIS CONTROL STOPPED REPRODUCING ITS OWN DEFECT and was rewritten on 2026-08-28. It used to clear
    every `broken_against`, "pretend everything passes", which disagreed with a page that said three
    were broken. Once the three were actually fixed the page said zero, the ledger said zero, and
    clearing an already-clear field injected nothing -- a green control over an unexercised checker.

    So it now breaks an adapter instead of mending one. That direction cannot go stale the same way:
    the page can only ever claim some number of broken adapters, and marking one more always
    disagrees with it.
    """
    p = sandbox / "docs" / "integration_conformance.json"
    import json
    d = json.loads(p.read_text(encoding="utf-8"))
    name, entry = sorted(d["integrations"].items())[0]
    entry["status"] = "broken"
    entry["broken_against"] = "9.9.9"
    entry["verified_against"] = None
    entry["detail"] = "injected by a control"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert "LIVE-MISMATCH" in _kinds(sandbox), (
        "breaking %s in the ledger did not disagree with the count published on the page" % name)


def test_CONTROL_an_unreadable_conformance_ledger_does_not_read_as_clean(sandbox):
    """"I checked and found nothing" and "I could not look" must not produce the same result."""
    (sandbox / "docs" / "integration_conformance.json").write_text("{ not json", encoding="utf-8")
    assert "LIVE-MISMATCH" in _kinds(sandbox)


def test_CONTROL_the_SERVER_gaining_a_tool_is_caught_too(sandbox):
    """The direction that actually bites: the code grows and the docs stay put.

    The previous control moves the DOCUMENT, which is the rarer accident. In practice a branch adds an
    `@mcp.tool()` and every published count silently becomes wrong at merge. That is not hypothetical:
    the count went 56 -> 58 -> 60 during this audit's own review, and on each bump exactly ONE of the
    four places that publish it was corrected by hand.

    The expected count is COMPUTED, never typed. The first version of this control asserted the literal
    "58" and broke the day the server reached 60 — a control about stale hardcoded counts, carrying one.
    """
    p = sandbox / "inspeximus" / "mcp_server.py"
    grown = _live_tool_count() + 2
    p.write_text(p.read_text(encoding="utf-8") + "\n\n@mcp.tool()\ndef newly_added_tool():\n    pass\n"
                 "\n\n@mcp.tool()\ndef another_new_tool():\n    pass\n", encoding="utf-8")
    problems = ca.audit_numbers(sandbox)[0]
    assert any(k == "LIVE-MISMATCH" and str(grown) in m for k, _w, m in problems), (
        f"the server grew to {grown} tools and every published count stayed behind, unnoticed; "
        f"got {problems}")


def test_CONTROL_a_number_hidden_in_an_html_attribute_is_still_seen(sandbox):
    """`data-count` RENDERS as the figure a visitor reads, so exempting it exempts the claim.

    The first version of the masker stripped tags before hoisting `data-count`, which silently excused
    the exact number that was wrong on the homepage. This control fails if that regression returns.
    """
    p = sandbox / "index.html"
    p.write_text(p.read_text(encoding="utf-8").replace(
        '<li><b>0</b><span>runtime dependencies</span></li>',
        '<li><b class="count" data-count="4242">0</b><span>invented metric</span></li>'),
        encoding="utf-8")
    kinds = _kinds(sandbox)
    assert "UNREGISTERED" in kinds, "a data-count figure was not read as published text"


def test_CONTROL_deleting_a_surface_file_does_not_read_as_clean(sandbox):
    """"I found no numbers" and "I could not look" produce the same empty list."""
    (sandbox / "MCP_LISTINGS.md").unlink()
    assert "MISSING-SURFACE" in _kinds(sandbox)


# ── the promise itself, not only the numbers behind it ──────────────────────────────────────────────
# Everything above checks that each published number IS registered. Nothing checked that the README
# still TELLS the reader so. Those are different properties, and a mutation that swapped
#   **Every number on this page is registered in [docs/CLAIMS.md](docs/CLAIMS.md)**
# for the vaguer
#   **Every number on this page traces to a runnable probe.**
# survived the whole suite: the registry was still correct, still complete, and no longer reachable
# from the page a reader lands on. The coverage guarantee is the one sentence that turns a number into
# a checkable number, so it is load-bearing in exactly the way an unverifiable promise is not.
#
# Deliberately narrow. It does not police every sentence containing "every number" -- three legitimate
# ones exist (the audit command's own help text, a description of a companion that FAILED audit, and a
# caveat in CLAIMS.md warning against precisely this over-read), and a lint that fires on honest prose
# gets switched off, which is the failure mode the release-notes lint documents.

_PROMISE = re.compile(r"\*\*\s*Every (?:number|figure) on this page\b[^*]*\*\*", re.I)


def test_the_readme_coverage_promise_names_the_registry():
    """A blanket promise about every number on the page must NAME the artifact that backs it.

    Fails in both directions: if the sentence is weakened to one that names no registry, and if it
    disappears (or is reworded past recognition) so the page makes no promise at all. An absent
    promise and a kept one must not read the same to this test -- that is the whole defect.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    promises = _PROMISE.findall(text)
    assert promises, (
        "README.md no longer carries a bolded 'Every number on this page ...' guarantee. Either it "
        "was removed, or it was reworded -- and a reworded guarantee is not silently equivalent: it "
        "is the sentence that tells a reader where to check us."
    )
    unbacked = [p for p in promises if "CLAIMS.md" not in p]
    assert not unbacked, (
        "the README promises something about every number on the page without naming the registry "
        f"that makes it checkable: {unbacked}. 'traces to a runnable probe' is a claim a reader "
        "cannot act on; '[docs/CLAIMS.md](docs/CLAIMS.md)' is."
    )


def test_CONTROL_a_coverage_promise_that_names_no_registry_is_caught(tmp_path):
    """The mutation that survived, reproduced on a copy. If this goes green the check is decorative."""
    real = (ROOT / "README.md").read_text(encoding="utf-8")
    weakened = real.replace(
        "**Every number on this page is registered in [docs/CLAIMS.md](docs/CLAIMS.md)**",
        "**Every number on this page traces to a runnable probe.**")
    assert weakened != real, "fixture no longer reproduces: the promise sentence has changed"
    found = _PROMISE.findall(weakened)
    assert found and all("CLAIMS.md" not in p for p in found), (
        "the weakened promise was not detected as unbacked -- the guard would have let the mutant through")


def test_CONTROL_a_deleted_coverage_promise_is_caught():
    """The other direction: removing the sentence must not read as compliance."""
    real = (ROOT / "README.md").read_text(encoding="utf-8")
    deleted = real.replace(
        "**Every number on this page is registered in [docs/CLAIMS.md](docs/CLAIMS.md)**", "")
    assert deleted != real, "fixture no longer reproduces: the promise sentence has changed"
    assert not _PROMISE.findall(deleted), (
        "a README with the guarantee deleted still looked like one that carries it")


# ── the published SUITE SIZE, and the controls that make its guard visible ──────────────────────────
# The badge lives inside a URL and scan_numbers does not read URLs, so `tests-2793` was published on the
# most-read line of the README with no gate over it at all. Registered-and-reproducible was never the
# same as still-true: the registry checks that a number has an entry and a resolving pin, and never runs
# the command, so 2,793 stayed green after the suite reached 2,797.
def _mini_root(tmp_path, readme_text, collected=2797):
    """A root carrying only what _live_consistency needs, with the count PRE-SEEDED.

    Seeded rather than collected: an 18-second subprocess per control would buy nothing here, because
    what these controls test is the comparison, not the counter. The counter has its own test below,
    which runs it for real against this repo.
    """
    root = tmp_path / "mini"
    (root / "inspeximus").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copy2(ROOT / "inspeximus" / "mcp_server.py", root / "inspeximus" / "mcp_server.py")
    (root / "README.md").write_text(readme_text, encoding="utf-8")
    ca._COLLECTED[root] = collected
    return root


def _live_msgs(root):
    return [m for kind, _where, m in ca._live_consistency(root) if kind == "LIVE-MISMATCH"]


def test_the_counter_really_counts_this_repo():
    """The counter itself, run for real. Everything below stubs it, so something must not."""
    n = ca._collected_test_count(ROOT)
    assert n is not None, "the suite could not be collected; the published count is unverified"
    assert n > 2000, f"the collected count came back as {n}, which is not a plausible size for this suite"


def _floor(kind):
    """Read the FLOOR the README publishes right now, rather than remembering a number.

    The first version of these controls pinned 2797 into the fixture. Eight new tests later the README
    legitimately said 2,804 and every control failed -- a guard about stale published numbers, carrying
    one. The repo already learned this on a control that hardcoded 56 MCP tools.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"badge/tests-([\d,]+)%2B-" if kind == "badge" else r"\\*\\*([\d,]+)\+ tests\\*\\*", text)
    assert m, f"fixture no longer reproduces: the {kind} test floor is not in the README"
    return m.group(1)


def test_the_counter_really_counts_this_repo():
    """The counter itself, run for real. Every control below stubs it, so something must not."""
    n = ca._collected_test_count(ROOT)
    assert n is not None, "the suite could not be collected; the published floor is unverified"
    assert n >= int(_floor("badge").replace(",", "")), (
        f"the suite collects {n}, below the floor this README publishes")


def test_CONTROL_a_suite_that_shrank_past_the_floor_is_caught(tmp_path):
    """The direction that would be a lie: fewer tests than we claim."""
    floor = int(_floor("badge").replace(",", ""))
    root = _mini_root(tmp_path, (ROOT / "README.md").read_text(encoding="utf-8"), collected=floor - 1)
    assert any("collects only" in m for m in _live_msgs(root)), _live_msgs(root)


def test_CONTROL_a_deleted_badge_does_not_read_as_clean(tmp_path):
    """Removing the number must not look like publishing a correct one."""
    now = _floor("badge")
    root = _mini_root(tmp_path, (ROOT / "README.md").read_text(encoding="utf-8")
                      .replace(f"badge/tests-{now}%2B-", "badge/tests-passing-"),
                      collected=int(now.replace(",", "")))
    assert any("tests badge is gone" in m for m in _live_msgs(root)), _live_msgs(root)


def test_CONTROL_an_uncountable_suite_does_not_read_as_agreement(tmp_path):
    """The failure this whole file is about: silence reported as a pass.

    If collection breaks, the honest output is "NOT checked". Reporting nothing would let a broken
    counter certify every number it never compared.
    """
    root = _mini_root(tmp_path, (ROOT / "README.md").read_text(encoding="utf-8"), collected=None)
    assert any("was NOT checked" in m for m in _live_msgs(root)), _live_msgs(root)


def test_CONTROL_a_suite_above_the_floor_produces_no_complaint(tmp_path):
    """The negative control. Without it, a guard that fires on everything passes every test above --
    and a floor that fired when the suite GREW would push us to understate it forever."""
    floor = int(_floor("badge").replace(",", ""))
    root = _mini_root(tmp_path, (ROOT / "README.md").read_text(encoding="utf-8"), collected=floor + 500)
    assert not [m for m in _live_msgs(root) if "test count" in m or "tests badge" in m], _live_msgs(root)


def test_CONTROL_an_absent_pytest_is_out_of_scope_and_a_present_one_is_not(tmp_path, monkeypatch):
    """The exemption, and the thing it must not swallow.

    The audit job installs no test extras, so demanding a collection there turned a healthy repository
    red for the absence of pytest. "The counting tool is not installed" and "the count failed" are
    different facts and only the second is a defect -- but an exemption without a control is how a guard
    stops seeing its target while still reporting green, so both directions are pinned here.
    """
    real = ca.importlib.util.find_spec

    monkeypatch.setattr(ca.importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "pytest" else real(name, *a, **k))
    root = _mini_root(tmp_path, (ROOT / "README.md").read_text(encoding="utf-8"), collected=None)
    assert not [m for m in _live_msgs(root) if "NOT checked" in m], (
        "with pytest absent the count is out of scope, not a failure")

    monkeypatch.setattr(ca.importlib.util, "find_spec", real)
    assert any("NOT checked" in m for m in _live_msgs(root)), (
        "with pytest PRESENT, a count that could not be produced is still a defect -- the exemption "
        "must not extend to it")


def test_the_published_floor_holds_in_the_leanest_environment_we_run():
    """The floor is a property of the repository; the COUNT is a property of the repository AND the
    environment. Measured: 2,804 locally with every optional extra, 2,637 on a CI runner without them,
    because modules that importorskip at module level are never collected at all. A floor set from the
    richest environment therefore calls a healthy lean one a shrinking suite -- which it did, on CI,
    for exactly one commit. This pins the headroom so the next person sees why the number understates."""
    floor = int(_floor("badge").replace(",", ""))
    assert floor <= 2637, (
        f"the published floor {floor} exceeds the 2,637 a runner without optional extras collects; "
        f"a floor must be true in the LEANEST environment we run, not the richest")
