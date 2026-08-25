"""The findings that were reported, reproduced, and deliberately NOT shipped for three rounds.

They were carried in the handoff as "known and unfixed" rather than quietly dropped, because each needed a
change bigger than the release it was found in. This file pins them now.

The largest is cross-process data loss. The store is one JSON file written whole and read once at open, so a
second handle won by writing last: the other writer's committed, `flush()`ed record was erased and
`verify_writes()` still returned True on both sides, because each surviving chain was self-consistent. That
is the worst possible shape for a library whose pitch is integrity — losing data and then certifying it.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── cross-process / multi-handle ────────────────────────────────────────────────────────────────────
def test_a_second_handle_cannot_silently_erase_the_first_ones_records():
    """Measured before: A wrote 'base', B wrote 'B-only', A wrote 'A-only' — and B's flushed record was gone
    from disk, with A.verify_writes() -> True."""
    p = _path()
    a = Inspeximus(path=p, receipts=True)
    a.remember("base fact")
    a.flush()

    b = Inspeximus(path=p, receipts=True)          # second handle, loaded before A's next write
    b.remember("B-only fact")
    b.flush()

    with pytest.raises(StoreChangedOnDisk):
        a.remember("A-only fact critical")

    on_disk = [r["text"] for r in json.load(open(p, encoding="utf-8"))]
    assert "B-only fact" in on_disk, "the other writer's record must survive the refusal"


def test_reload_merges_both_writers_rather_than_picking_one():
    """The recovery path. Neither side should have to lose a write to resolve the conflict."""
    p = _path()
    a = Inspeximus(path=p, receipts=True)
    a.remember("base fact")
    a.flush()
    b = Inspeximus(path=p, receipts=True)
    b.remember("B-only fact")
    b.flush()
    with pytest.raises(StoreChangedOnDisk):
        a.remember("A-only fact")

    res = a.reload()
    assert res["reloaded"] == 2 and res["readded"] >= 1
    on_disk = [r["text"] for r in json.load(open(p, encoding="utf-8"))]
    assert {"base fact", "B-only fact", "A-only fact"} <= set(on_disk)


def test_a_single_writer_is_never_told_it_conflicts_with_itself():
    """A false conflict on the ordinary path would be worse than the bug — everyone would disable the check."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    for i in range(25):
        m.remember(f"record {i}")
    m.flush()
    assert len(json.load(open(p, encoding="utf-8"))) == 25

    reopened = Inspeximus(path=p, receipts=True)   # sequential handles are not concurrent ones
    reopened.remember("after reopen")
    reopened.flush()
    assert len(json.load(open(p, encoding="utf-8"))) == 26


# ── the store file must stay valid JSON ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field", ["value", "valid_from"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_number_is_refused_at_the_write(field, bad):
    """`json.dumps` wrote a bare NaN/Infinity literal: Python re-reads it, every strict parser (jq, JS,
    serde) rejects the file, and `state_digest`/`verify_writes` both still reported healthy. `inf` also
    sorted first in every recall forever; `nan` never compared true, so the record sank silently."""
    m = Inspeximus(path=_path())
    with pytest.raises(ValueError, match="finite"):
        m.remember("x", **{field: bad})


def test_a_non_finite_value_that_bypassed_the_write_guard_still_cannot_reach_the_file():
    """Second layer, and it needs its own test because the first makes it unreachable through the public API.
    A NaN can still arrive by another route — a hand-edited record, a field the write guard does not cover, a
    future write path — and `json.dumps` without allow_nan=False would put a bare literal in the file."""
    m = Inspeximus(path=_path())
    m.remember("ordinary record")
    m._items[0]["value"] = float("nan")            # planted past remember()'s validation
    m._save(force=True)                            # the hot path does not raise, by design...
    ok, problems = m.verify_writes()
    assert ok is False and any("not persisted" in x for x in problems)   # ...it reports
    with pytest.raises(OSError):                                          # ...and flush() raises
        m.flush()
    assert "NaN" not in open(m.path, encoding="utf-8").read()             # nothing invalid reached the file


def test_the_store_file_parses_under_a_strict_json_reader():
    m = Inspeximus(path=_path())
    m.remember("ordinary record", value=2.5)
    m.flush()
    raw = open(m.path, encoding="utf-8").read()
    json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"non-finite: {c}")))


# ── foreign / older records ─────────────────────────────────────────────────────────────────────────
def test_a_record_missing_newer_fields_does_not_crash_or_miscount():
    """A hand-edited, foreign or pre-upgrade record raised a bare `KeyError: 'status'` in six methods — and
    made `index_coherence` report `coherent: true` with an UNDERCOUNT, which is worse than crashing."""
    p = _path()
    json.dump([{"id": "x1", "text": "legacy record", "ts": 1.0, "value": 1.0}],
              open(p, "w", encoding="utf-8"))
    m = Inspeximus(path=p)

    assert [h["text"] for h in m.recall("legacy")] == ["legacy record"]
    assert m.memory_report()["total"] == 1
    assert m.index_coherence()["active_text_records"] == 1
    assert isinstance(m.contradictions(), list)
    m.consolidate()


def test_normalisation_does_not_overwrite_fields_that_are_present():
    p = _path()
    json.dump([{"id": "x1", "text": "t", "ts": 1.0, "value": 7.0, "status": "superseded",
                "tags": ["keep"], "meta": {"a": 1}}], open(p, "w", encoding="utf-8"))
    r = Inspeximus(path=p).items[0]
    assert (r["value"], r["status"], r["tags"], r["meta"]) == (7.0, "superseded", ["keep"], {"a": 1})


# ── the erasure path is reachable from every surface ────────────────────────────────────────────────
def test_the_cli_can_perform_a_subject_erasure():
    """The library has had subject erasure since 1.0; the CLI never exposed it, so the one operation a DSAR
    actually needs was unreachable from the terminal."""
    import subprocess
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    m.remember("alice ssn 123", source={"doc": "alice"})
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p,
                          "forget-subject", "alice", "--request-id", "DSAR-1"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "erased 1" in out.stdout, out.stdout + out.stderr
    assert Inspeximus(path=p).items == []


@pytest.mark.parametrize("module,func", [
    ("inspeximus.mcp_server", "forget_pii"),
    ("inspeximus.integrations.google_adk", "forget_subject_for"),
    ("inspeximus.integrations.openai_agents", "forget_subject"),
])
def test_every_erasure_surface_has_the_ambiguity_escape_hatch(module, func):
    """A guard with no override turns a legitimate GDPR erasure into an unreachable one. Three surfaces
    shipped without it after the guard landed."""
    import ast
    import pathlib
    path = pathlib.Path(__file__).parent.parent / (module.replace(".", "/") + ".py")
    node = next(n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef) and n.name == func)
    assert "allow_ambiguous" in [a.arg for a in node.args.args]
