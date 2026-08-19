"""A store too big to hold is read through its index, so a record whose line does not distinguish it
is present, correct, and never opened.

WHERE THE DESIGN COMES FROM. Measured on a 316-note store with 120 questions written from the note
bodies and shown to no line-writer, ranking all 316 candidates on two query registers (full questions,
and the three-to-eight words someone types into a search box). recall@3:

    hand-written title + hook   0.333 / 0.508      title alone            0.300 / 0.450
    title + highest-idf terms   0.350 / 0.533      what it CONCLUDED      0.683 / 0.833
    ceiling, the full records   0.858 / 0.967

Two of those rows are why the summariser is a PARAMETER rather than a dependency: the only variant
that moved the number is a written sentence, and the deterministic one -- stuffing the line with the
record's most distinctive terms -- is a null on both registers, +0.017 and +0.025 with both intervals
containing zero. A register control settled the shape of the sentence: a question-form line scored
0.775 on question-form queries, but both had been written by the same model from the same text, and
57% of that margin vanished on search-box queries while the written line did not move.
"""
from __future__ import annotations

import os
import tempfile

from inspeximus import Inspeximus


def _store(**kw):
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)
    ix.remember("The deploy gate blocks on a stale lockfile; the timeout went to 90s.", key="deploy")
    ix.remember("Salary review moved to April after the board meeting shifted.", key="salary")
    ix.remember("Postgres COLLATE made two user names compare equal on staging only.", key="collate")
    ix.flush()
    return ix


# ───────────────────────────────────────── the default is honest about being the weak one
def test_without_a_summariser_it_says_which_variant_it_is_giving_you():
    """A caller handed the weaker option without being told has been given a number they will read
    as the product's answer. The figure quoted must be the one THIS function was measured at -- the
    first version of this test asserted 0.300, which is the experiment's `title alone` row and not
    what this default produces; measured through the function, the opening sentence gives 0.442."""
    r = _store().memory_index()
    assert r["records"] == 3 and r["fallback"] == 3 and r["generated"] == 0
    assert any("0.442" in x and "0.692" in x for x in r["limits"]), r["limits"]
    assert any("ESTIMATED" in x for x in r["limits"]), "a token figure with no tokenizer must say so"


def test_a_summariser_writes_the_line_and_the_line_is_kept():
    calls = []

    def summarise(text):
        calls.append(text)
        return "concluded that " + text.split(";")[0]

    ix = _store()
    first = ix.memory_index(summarise=summarise)
    assert first["generated"] == 3 and first["reused"] == 0 and len(calls) == 3
    assert all("concluded that" in ln for ln in first["lines"])

    second = ix.memory_index(summarise=summarise)
    assert second["reused"] == 3 and second["generated"] == 0
    assert len(calls) == 3, "a stored line was rewritten: every re-read would cost a model call"
    assert second["lines"] == first["lines"]

    again = ix.memory_index(summarise=summarise, refresh=True)
    assert again["generated"] == 3 and len(calls) == 6, "refresh must actually regenerate"


# ───────────────────────────────────────── the budget shortens, it does not delete
def test_a_budget_shortens_lines_and_never_drops_a_record():
    """MUST-FAIL CONTROL for the obvious wrong implementation. Dropping records is the easy way to
    meet a budget and it is the one thing the index must never do: a record with no line cannot be
    found at all, which is strictly worse than a record with a short one."""
    ix = _store()
    ix.memory_index(summarise=lambda t: "concluded that " + t)

    # A budget that SHORTENING ALONE CAN REACH. The first version of this test used only this case,
    # and a mutant that drops records to fit survived it -- the drop path never had to run. The
    # fixture has to make the wrong implementation act before it can catch it.
    roomy = ix.memory_index(budget_tokens=20)
    assert roomy["records"] == 3 and roomy["tokens_estimate"] <= 20 and roomy["over_budget"] == 0

    # And one it CANNOT: every line is already at its floor, so the only way to fit is to delete.
    tight = ix.memory_index(budget_tokens=8)
    assert tight["records"] == 3, "a record was dropped to meet the budget"
    assert len(tight["lines"]) == 3
    assert tight["over_budget"] > 0, "this fixture no longer forces the choice it exists to test"
    assert tight["shortened"] == 3
    assert any("None were removed" in x for x in tight["limits"])
    assert any("COULD NOT BE MET" in x for x in tight["limits"]),         "a budget silently exceeded is one the caller planned around"
    for key in ("deploy", "salary", "collate"):
        assert any(ln.startswith("- %s" % key) for ln in tight["lines"]), \
            "%s is not in the index and is therefore unreachable" % key


def test_an_impossible_budget_still_lists_every_record():
    """The floor case of the same rule: even a budget that no summary can fit keeps every key."""
    ix = _store()
    r = ix.memory_index(budget_tokens=1)
    assert r["records"] == 3 and len(r["lines"]) == 3
    for key in ("deploy", "salary", "collate"):
        assert any(key in ln for ln in r["lines"])


# ───────────────────────────────────────── a broken summariser must not cost reachability
def test_a_summariser_that_returns_nothing_falls_back_rather_than_leaving_a_blank():
    ix = _store()
    r = ix.memory_index(summarise=lambda t: "")
    assert r["fallback"] == 3 and r["generated"] == 0
    assert all(len(ln) > len("- deploy — ") for ln in r["lines"]), "a blank line is an unreachable record"


def test_a_summariser_that_raises_does_not_take_the_index_down():
    """An index is read at the start of a session. If a model call failing there raised, the whole
    store would go dark for the sake of one line."""
    def boom(_text):
        raise RuntimeError("model unavailable")

    r = _store().memory_index(summarise=boom)
    assert r["records"] == 3 and r["fallback"] == 3
    assert all("—" in ln for ln in r["lines"])


# ───────────────────────────────────────── it is a per-tenant answer
def test_a_tenant_indexes_only_its_own_records():
    """The index names keys, and a key is exactly what must not cross a tenant boundary."""
    root = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    root.for_tenant("acme").remember("acme only", key="acme-key")
    root.for_tenant("globex").remember("globex only", key="globex-key")
    root.flush()
    r = root.for_tenant("acme").memory_index()
    assert r["records"] == 1
    assert "globex" not in str(r)


# ───────────────────────────────────────── the agent is its own summariser, over MCP
def test_an_agent_can_write_its_own_index_lines():
    """A model reaching this library over MCP cannot pass a `summarise` callable. It can read which
    records still need a line, write them, and store them -- paying once per record instead of once
    per session."""
    ix = _store()
    first = ix.memory_index()
    assert {x["key"] for x in first["needs_line"]} == {"deploy", "salary", "collate"}
    assert all(x["text"] for x in first["needs_line"]), "a key with no text cannot be summarised"

    out = ix.set_index_line("deploy", "concluded a stale lockfile was the gate, fixed by a 90s timeout")
    assert out["stored"] is True and out["records"] == 1

    second = ix.memory_index()
    assert second["reused"] == 1
    assert {x["key"] for x in second["needs_line"]} == {"salary", "collate"}
    assert any("stale lockfile" in ln for ln in second["lines"])


def test_an_empty_index_line_is_refused_rather_than_stored():
    """Storing "" would make the record unreachable while looking like the index was filled in."""
    ix = _store()
    for blank in ("", "   ", "\n\t"):
        out = ix.set_index_line("deploy", blank)
        assert out["stored"] is False, "a blank line was accepted for %r" % blank
        assert "unreachable" in out["reason"]
    assert ix.memory_index()["fallback"] == 3


def test_the_index_line_key_is_reserved_against_the_caller():
    """A caller who could set another record's index line could make it unfindable while the store
    still reported it present and correct. The docstring claimed this keyspace before the key was
    actually in the set, which is the defect this test exists to keep fixed."""
    from inspeximus.core import _RESERVED_META
    assert "index_line" in _RESERVED_META
    ix = _store()
    ix.remember("a note a caller tried to hide", key="hidden", meta={"index_line": "nothing to see"})
    ix.flush()
    r = ix.memory_index()
    assert not any("nothing to see" in ln for ln in r["lines"]), "a caller set an index line directly"
    assert r["fallback"] == 4


def test_setting_a_line_on_another_tenants_key_does_not_reach_it():
    """An index line any writer could set on any record is a way to make someone else's memory
    unfindable, which is why it lives in the reserved keyspace and is scoped like everything else."""
    root = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    root.for_tenant("acme").remember("acme only", key="shared-key")
    root.for_tenant("globex").remember("globex only", key="shared-key")
    root.flush()
    out = root.for_tenant("acme").set_index_line("shared-key", "acme's own line")
    assert out["stored"] is True and out["records"] == 1, "it reached more than its own tenant's record"
    other = root.for_tenant("globex").memory_index()
    assert "acme's own line" not in str(other)
    assert other["fallback"] == 1
