"""The CLI's write paths, and whether what they write can be reached by the person it is about.

Separate file, deliberately, with NO `importorskip`. The CLI needs nothing optional; these tests were
first written inside `test_mcp_provenance_reach.py`, and when that file gained `pytest.importorskip("mcp")`
to stop the base CI job erroring, it took the CLI tests down with it — coverage silently narrowed by a fix
for something else. A guard belongs to the dependency it guards, not to the file it happens to be in.

`decision` was the last surface that could not attribute what it wrote. Core accepted `source` and the MCP
tool passed it; the CLI took only `--because` and `--topic`, and a decision is usually ABOUT someone, so it
was the record most likely to survive a DSAR that erased everything else about that person. Measured
before the fix:

    inspeximus decision "bill alice monthly" --topic billing::alice
      forget-subject hr/alice   ->  would erase 0 record(s)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cli(store_path, *args):
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(store_path), *args],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout


def test_distill_offers_a_source():
    """`core.distill_and_remember` has taken a `source` all along; the CLI never offered one, so a
    transcript distilled through it produced records attributable to nothing. Found by READING the branch
    -- the scan that flagged it also flagged two subcommands that do not write at all.

    Asserts the PARSER, not the source text: the flag has to be reachable by a user, which is a property
    of the argparse tree and not of how the branch happens to be written."""
    import inspeximus.cli as cli
    parser = cli._build_parser() if hasattr(cli, "_build_parser") else None
    if parser is None:                      # parser is built inline; fall back to running --help
        out = _cli("ignored.json", "distill", "--help")
        assert "--source" in out
        return
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "distill" in a.choices)
    assert "--source" in {o for act in sub.choices["distill"]._actions for o in act.option_strings}


def test_a_decision_with_a_source_is_erasable_by_subject(tmp_path):
    """THE defect. A decision about a person must be reachable by that person."""
    s = tmp_path / "s.json"
    _cli(s, "--json", "decision", "bill alice monthly", "--topic", "billing::alice",
         "--source", "hr/alice")
    out = _cli(s, "forget-subject", "hr/alice", "--dry-run")
    assert "would erase 1 record(s)" in out
    assert "1 naming the subject" in out


def test_a_decision_without_a_source_stays_unreachable(tmp_path):
    """CONTROL, and the design decision: the caller supplies the subject, the CLI never invents one.
    It also proves the test above is not passing vacuously."""
    s = tmp_path / "s.json"
    _cli(s, "--json", "decision", "bill bob monthly", "--topic", "billing::bob")
    assert "would erase 0 record(s)" in _cli(s, "forget-subject", "hr/bob", "--dry-run")


def test_a_decision_keeps_keyed_supersession_when_a_source_is_added(tmp_path):
    """CONTROL on the other axis. Threading provenance must not disturb what `decision` is FOR."""
    s = tmp_path / "s.json"
    _cli(s, "--json", "decision", "bill alice monthly", "--topic", "billing::alice",
         "--source", "hr/alice")
    _cli(s, "--json", "decision", "bill alice yearly", "--topic", "billing::alice",
         "--source", "hr/alice")
    hits = _cli(s, "--json", "recall", "billing alice")
    assert "yearly" in hits and "monthly" not in hits, "the superseded decision must not be served"


def test_a_decision_can_declare_a_parent(tmp_path):
    s = tmp_path / "s.json"
    parent = json.loads(_cli(s, "--json", "remember", "alice contract terms",
                             "--source", "hr/alice"))["id"]
    _cli(s, "--json", "decision", "renew on those terms", "--topic", "renewal::alice",
         "--source", "summary-svc", "--derived-from", parent)
    out = _cli(s, "forget-subject", "hr/alice", "--dry-run")
    assert "would erase 2 record(s)" in out
    assert "1 reached through lineage" in out


# ── the CLI `remember` arms, moved here from the mcp-guarded file ──────────────────────────────────
def test_remember_could_name_a_source_but_never_a_parent(tmp_path):
    """`--source` existed, so subject erasure worked, but there was no way to declare LINEAGE — so a
    summary built from a person's file survived the erasure of that file."""
    s = tmp_path / "s.json"
    parent = json.loads(_cli(s, "--json", "remember", "alice file", "--source", "hr/alice"))["id"]
    _cli(s, "--json", "remember", "summary of alice", "--source", "summary-svc",
         "--derived-from", parent)
    out = _cli(s, "forget-subject", "hr/alice", "--dry-run")
    assert "would erase 2 record(s)" in out and "1 reached through lineage" in out


def test_remember_without_a_parent_reaches_only_the_direct_record(tmp_path):
    """CONTROL. A cascade that fired without a declared edge would be erasing on a guess."""
    s = tmp_path / "s.json"
    _cli(s, "--json", "remember", "alice file", "--source", "hr/alice")
    _cli(s, "--json", "remember", "summary of alice", "--source", "summary-svc")
    assert "would erase 1 record(s)" in _cli(s, "forget-subject", "hr/alice", "--dry-run")
