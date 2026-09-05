"""A capability documented only in the README cannot be found.

Measured 2026-09-05. GitHub repository search reads the repo NAME, DESCRIPTION and TOPICS. It does
not read the README. Established by control, not assumed: "windsurf" and "cline" appear only in our
description and both queries return us; "supersede" is a topic and returns us at position 16;
"erasure" appears thirteen times in the README and did not return us anywhere in the top 300 of a
query with ten total results.

Before the fix, 2 of 15 implemented capabilities were reachable by search, and the two that ranked
were exactly the two carrying a topic. Nine lived only in the README.

These tests pin the surfaces a searcher can actually reach. They do NOT require every capability to
appear: `title` and `description` are capped at 100 characters each by the MCP registry schema, so a
test demanding all fifteen would be a gate that can never pass. They require the DIFFERENTIATORS,
the capabilities competitors do not offer, plus the length limits that make the budget real.

`probes/which_of_our_capabilities_a_searcher_can_actually_find.py` measures the full picture live.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_JSON = ROOT / "server.json"

# The registry caps both fields at 100. Ours are 87 and 94, so the budget is real and a new term
# costs an old one. This is why the list below is short and argued rather than exhaustive.
CAP = 100

# What a person looking for THIS product, rather than for agent memory in general, would type.
# Each is implemented: `erasure_*` tools, `provenance`, `audit_bundle`, `forget_subject`, `revert`.
DIFFERENTIATORS = ("erasure", "provenance", "audit", "revert", "supersede", "forget", "gdpr")


def _server():
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


def _surface(d):
    return ((d.get("title") or "") + " " + (d.get("description") or "")).lower()


def test_the_title_field_is_used_at_all():
    """An empty `title` is 100 characters of discovery surface spent on nothing."""
    t = _server().get("title") or ""
    assert t.strip(), "server.json has no title; the registry renders the bare name instead"


@pytest.mark.parametrize("field", ["title", "description"])
def test_each_field_fits_the_registry_cap(field):
    v = _server().get(field) or ""
    assert len(v) <= CAP, "%s is %d characters; the registry rejects anything over %d" % (
        field, len(v), CAP)


@pytest.mark.parametrize("word", DIFFERENTIATORS)
def test_a_differentiator_is_named_where_search_can_read_it(word):
    assert word in _surface(_server()), (
        "%r appears in neither the title nor the description, so a directory search for it cannot "
        "return us. The README does not count: search does not read it." % word)


def test_CONTROL_the_check_fails_when_a_term_is_absent():
    """Without this, a check that matched everything would pass silently and prove nothing."""
    d = _server()
    surface = _surface(d)
    assert "zzqqxx-not-a-capability" not in surface, "the fixture is contaminated"
    missing = [w for w in ("zzqqxx-not-a-capability",) if w not in surface]
    assert missing, "the membership test cannot report an absence, so every pass above is vacuous"


def test_the_registry_description_does_not_merely_repeat_the_title():
    """Two fields carrying the same words are one field. They must earn separate terms."""
    d = _server()
    t = set((d.get("title") or "").lower().split())
    s = set((d.get("description") or "").lower().split())
    assert s - t, "the description adds no word the title does not already carry"
