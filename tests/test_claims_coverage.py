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
        shutil.copy2(ROOT / name, dst / name)
    (dst / "inspeximus").mkdir()
    shutil.copy2(ROOT / "inspeximus" / "mcp_server.py", dst / "inspeximus" / "mcp_server.py")
    for sub in ("probes", "docs", "tools", "benchmarks"):
        (dst / sub).mkdir()
    # Everything a reproduction command can name has to exist here, or BROKEN-COMMAND fires on an
    # untouched sandbox and every control that asserts "clean before, dirty after" measures nothing.
    for src, pat in ((ROOT / "probes", "*.py"), (ROOT / "tools", "*.py")):
        for p in src.glob(pat):
            (dst / src.name / p.name).touch()
    for rel in ("benchmarks/locomo/run.py", "docs/CLAIMS.md"):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).touch()
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
                              cwd=str(ROOT), capture_output=True, text=True, env=env)

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
    found = [t for _ln, t, _src in ca.scan_numbers(sandbox / "README.md")]
    assert "90" in found
    p = sandbox / "README.md"
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
    p = sandbox / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "13 passed · 0 FAILED · 0 skipped · 5 not testable here", "the audit passed"), encoding="utf-8")
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
    """The homepage advertised nine adapters unqualified while the ledger recorded three broken."""
    p = sandbox / "docs" / "integration_conformance.json"
    import json
    d = json.loads(p.read_text(encoding="utf-8"))
    for v in d["integrations"].values():
        v["broken_against"] = None          # pretend everything passes; the page still says 3 broken
    p.write_text(json.dumps(d), encoding="utf-8")
    assert "LIVE-MISMATCH" in _kinds(sandbox)


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
