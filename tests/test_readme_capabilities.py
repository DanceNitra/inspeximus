"""The reader-facing surface may not name a capability the code does not have.

WHY THIS EXISTS. `MCP_LISTINGS.md` advertised "30 tools" while `inspeximus/mcp_server.py` registered 56,
and it had been wrong in the same way once before (12 -> 30). The README's `## Status` block said `v0.2`
against a v1.89.0 package, and five of its own "Jump to" anchors pointed at headings that no longer
existed. None of that is a code defect and every one of it is the first thing a reader meets. A number or
a name maintained by hand drifts on exactly the schedule you stop watching it.

WHAT IT CHECKS. Four surfaces, each against the code rather than against a copy of itself:

  1. every ``name(`` / ``m.name`` token in README.md and docs/*.md resolves to something inspeximus
     actually exposes -- an MCP tool, an `Inspeximus` method, a package export, or a public function of a
     shipped submodule;
  2. every ``inspeximus <subcommand>`` shell claim resolves to a real argparse subcommand;
  3. the tool names listed in MCP_LISTINGS.md are exactly the tools `mcp_server.py` registers, and
  4. its advertised tool COUNT equals the real one.

HOW IT AVOIDS REPORTING SAFE WHILE SEEING NOTHING. Every check that walks a set first asserts that set is
non-empty and of a plausible size, and `test_the_guard_fires_on_an_invented_capability` is a negative
control: it feeds the checker a README that names a capability nobody ships and requires the checker to
catch it. If the extraction regex, the surface union, or the doc layout ever changes so that the checker
inspects nothing, that control goes red instead of the suite going quietly green -- which is the whole
difference between "the fix works" and "the case never arises".
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
LISTINGS = os.path.join(ROOT, "MCP_LISTINGS.md")
DOCS = os.path.join(ROOT, "docs")

#: Submodules whose public functions are part of the documented surface.
_SUBMODULES = (
    "audit_bundle", "code_guard", "compliance", "erasure_auditor", "erasure_residue",
    "witness_pool", "witness_server", "deletion_manifest", "claude_code", "install", "cli",
)

#: Identifiers that appear in the docs on purpose but belong to somebody ELSE's API. Each one is here
#: with the system it belongs to, because "unknown name" and "deliberately quoted foreign name" are
#: different facts and collapsing them is how an allow-list turns into a place defects go to hide.
FOREIGN_API = {
    "add": "mem0 -- `add()` is the call whose LLM extraction we contrast with our write path",
    "add_episode": "Zep/Graphiti -- their per-episode LLM entity/edge extraction",
    "delete": "mem0/others -- the unverified delete we contrast with `forget` + a receipt",
    "reset": "mem0 -- `reset()` is what purges its history table",
    "search": "generic vector-store API named when describing other systems",
    "update": "mem0 -- `update()` in its lifecycle",
    "invoke": "LangChain -- `.invoke()` on a runnable, called on our adapter, not defined by us",
    "save": "CrewAI storage protocol -- the method name their interface requires",
    "register": "framework registry calls shown in the integration snippets",
}

#: Names that are types, decorators or documented constructor-level things rather than capabilities.
NON_CAPABILITY = {"Inspeximus", "ComplianceMixin", "InspeximusRetriever", "InspeximusStore"}

#: Python builtins written inside a formula or snippet. `min(len(followups), max_followups)` describes an
#: arithmetic bound, not something inspeximus exposes, and flagging it teaches the reader to ignore the
#: guard. Kept as an explicit, enumerated set rather than "anything importable", so a real capability
#: cannot slip through by happening to share a name with something in the interpreter.
BUILTINS = {
    "min", "max", "len", "int", "float", "str", "list", "dict", "set", "sum", "abs", "round",
    "sorted", "range", "print", "open", "type", "bool", "tuple", "zip", "any", "all", "enumerate",
}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------------------------------
# the surface, read off the code
# --------------------------------------------------------------------------------------------------
def mcp_tool_names():
    """The tools `inspeximus-mcp` registers, parsed from the decorator that registers them.

    Parsed rather than imported so this runs without the optional `[mcp]` dependency installed.
    """
    src = _read(os.path.join(ROOT, "inspeximus", "mcp_server.py"))
    names = re.findall(r"@mcp\.tool\(\)\s*\n\s*(?:async\s+)?def\s+([A-Za-z_0-9]+)", src)
    assert len(names) > 20, (
        "parsed %d MCP tools -- the decorator pattern changed and this guard is now reading an "
        "empty surface, which would let ANY name pass" % len(names)
    )
    assert len(names) == len(set(names)), "duplicate MCP tool names: %r" % (
        sorted({n for n in names if names.count(n) > 1}),)
    return set(names)


def cli_subcommands():
    src = _read(os.path.join(ROOT, "inspeximus", "cli.py"))
    subs = set(re.findall(r'add_parser\(\s*"([a-z0-9][a-z0-9-]*)"', src))
    assert len(subs) > 10, "parsed %d CLI subcommands -- the parser shape changed" % len(subs)
    return subs


def _integration_names():
    """Top-level `def`/`class` in the framework adapters, read from source.

    Source-parsed rather than imported: every adapter needs its framework installed, and a guard that
    quietly skipped the adapters when LangChain was absent would stop seeing exactly the names most
    likely to rot. `inspeximus_toolset` (Pydantic AI) and `forget_subject_for` (Google ADK) are both
    real and both invisible to an import-based scan in a bare environment.
    """
    import ast
    out, d = set(), os.path.join(ROOT, "inspeximus", "integrations")
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if not fn.endswith(".py"):
            continue
        try:
            tree = ast.parse(_read(os.path.join(d, fn)))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    out.add(node.name)
                if isinstance(node, ast.ClassDef):
                    out |= {b.name for b in node.body
                            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not b.name.startswith("_")}
    assert len(out) > 10, "parsed %d integration names -- the adapters moved" % len(out)
    return out


def _self_attributes():
    """`self.<name> = ...` in core.py. `store.last_write` is documented and is set inside `remember()`,
    so it exists on no fresh instance and on no class -- an attribute-only scan would call it a defect."""
    src = _read(os.path.join(ROOT, "inspeximus", "core.py"))
    names = set(re.findall(r"\bself\.([a-z][A-Za-z_0-9]*)\s*=", src))
    assert len(names) > 10, "parsed %d self-attributes from core.py" % len(names)
    return names


def public_surface():
    """Every name a reader could legitimately write in backticks: MCP tools, `Inspeximus` methods AND
    instance attributes, keyword parameters (a config flag like `echo_guard` is a capability too),
    package exports, shipped submodules, and the framework adapters."""
    import inspect

    import inspeximus
    from inspeximus import Inspeximus

    names = set(mcp_tool_names())
    names |= {n for n in dir(Inspeximus) if not n.startswith("_")}
    names |= {n for n in dir(Inspeximus()) if not n.startswith("_")}   # instance attrs set in __init__
    names |= {n for n in dir(inspeximus) if not n.startswith("_")}
    for mod in _SUBMODULES:
        try:
            m = __import__("inspeximus.%s" % mod, fromlist=["*"])
        except Exception:                                     # optional dep, not a surface question
            continue
        names |= {n for n in dir(m) if not n.startswith("_")}
    for n in list(names):
        try:
            f = getattr(Inspeximus, n, None)
            if callable(f):
                names |= {p for p in inspect.signature(f).parameters if not p.startswith("_")}
        except (TypeError, ValueError):
            pass
    names |= _integration_names()
    names |= _self_attributes()

    # ASSERT THE TARGET RESOLVES. A union this guard cannot build is a union that green-lights
    # everything, so pin both its size and a few names that must be in it by construction.
    assert 100 < len(names) < 1200, "public surface resolved to %d names" % len(names)
    for anchor in ("provenance", "revert", "history", "erasure_certificate", "why_recalled",
                   "echo_guard", "inspeximus_toolset", "last_write"):
        assert anchor in names, "%r is missing from the resolved surface -- the guard is misaimed" % anchor
    return names


# --------------------------------------------------------------------------------------------------
# the checker (a pure function, so the negative control can drive it)
# --------------------------------------------------------------------------------------------------
#: A capability name is snake_case and starts lowercase. `P(detected)` in a probability expression and
#: `_store` in an internals aside are not capability claims, and treating them as such would train the
#: next person to widen the allow-list instead of reading the finding.
CALL_RE = re.compile(r"`([a-z][a-z_0-9]*)\(")
ATTR_RE = re.compile(r"`(?:m|mem|store|self)\.([a-z][a-z_0-9]*)")
#: Shell claims are read only from fenced code blocks. In prose, "inspeximus already does X" matches a
#: bare `inspeximus \w+` pattern and the guard then reports `already` as a missing subcommand -- noise
#: that buries the one real finding.
FENCE_RE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
SHELL_RE = re.compile(r"^\s*(?:\$ )?inspeximus\s+([a-z][a-z0-9-]*)", re.M)


def capability_tokens(text):
    """Capability-shaped tokens a reader would take as "this library has this"."""
    toks = set(CALL_RE.findall(text)) | set(ATTR_RE.findall(text))
    return toks - NON_CAPABILITY - set(FOREIGN_API) - BUILTINS


def shell_claims(text):
    """`inspeximus <subcommand>` as it appears in a runnable block."""
    claims = set()
    for block in FENCE_RE.findall(text):
        claims |= set(SHELL_RE.findall(block))
    return claims


def unknown_capabilities(text, surface):
    """The point of the whole file: names the prose claims that the code does not expose."""
    return sorted(t for t in capability_tokens(text) if t not in surface)


# --------------------------------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------------------------------
def test_readme_names_only_capabilities_that_exist():
    text = _read(README)
    surface = public_surface()
    toks = capability_tokens(text)
    assert len(toks) >= 20, (
        "extracted only %d capability tokens from the README (%r) -- the extraction, not the README, "
        "is what broke; a guard reading nothing reports SAFE" % (len(toks), sorted(toks))
    )
    assert "provenance" in toks, "the README no longer names `provenance()` -- extraction is misaimed"
    missing = unknown_capabilities(text, surface)
    assert not missing, (
        "README.md claims capabilities that are not in the MCP tool list or the public API: %r. "
        "Either ship them, rename them to what exists, or -- if the name belongs to another system -- "
        "add it to FOREIGN_API with the system it belongs to." % missing
    )


@pytest.mark.parametrize("doc", sorted(
    f for f in os.listdir(DOCS) if f.endswith(".md")) if os.path.isdir(DOCS) else [])
def test_docs_name_only_capabilities_that_exist(doc):
    text = _read(os.path.join(DOCS, doc))
    missing = unknown_capabilities(text, public_surface())
    assert not missing, "docs/%s claims capabilities that do not exist: %r" % (doc, missing)


def test_readme_shell_claims_resolve_to_real_subcommands():
    text = _read(README)
    subs = cli_subcommands()
    claimed = shell_claims(text)
    assert len(claimed) >= 5, "extracted only %d shell claims -- extraction misaimed" % len(claimed)
    for anchor in ("residue", "erasure-audit", "audit-verify"):
        assert anchor in claimed, (
            "the README no longer shows `inspeximus %s` in a runnable block -- either the page dropped "
            "its headline command or this extraction stopped seeing fenced blocks" % anchor)
    unknown = sorted(c for c in claimed if c not in subs and c != "-mcp")
    assert not unknown, "README shows `inspeximus %s` but the CLI has no such subcommand" % unknown


def test_mcp_listings_tool_list_matches_the_server():
    """MCP_LISTINGS.md is what registries copy. It said 30 while the server registered 56."""
    text = _read(LISTINGS)
    real = mcp_tool_names()
    body = text.split("## Where to submit")[0]
    listed = {t for t in re.findall(r"\b([a-z][a-z0-9_]{2,})\b", body) if t in real}
    assert len(listed) > 20, "found only %d tool names in MCP_LISTINGS.md -- extraction misaimed" % len(listed)
    missing = sorted(real - listed)
    assert not missing, "MCP_LISTINGS.md omits tools the server registers: %r" % missing


def test_mcp_listings_tool_count_matches_the_server():
    text = _read(LISTINGS)
    n = len(mcp_tool_names())
    counts = {int(x) for x in re.findall(r"(?:server \(`inspeximus-mcp`, |\*\*Tools \()(\d+)", text)}
    assert counts, "MCP_LISTINGS.md no longer states a tool count in a form this guard can read"
    assert counts == {n}, (
        "MCP_LISTINGS.md advertises %r tools; the server registers %d" % (sorted(counts), n))


def test_readme_internal_anchors_resolve():
    """Five 'Jump to' links pointed at headings that had been renamed away."""
    text = _read(README)
    heads = [l for l in text.split("\n") if re.match(r"^#{2,4} ", l)]
    assert len(heads) > 10, "parsed %d headings -- anchor check is misaimed" % len(heads)

    def slug(h):
        s = re.sub(r"^#+\s+", "", h).strip().lower()
        s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
        return s.replace(" ", "-")                       # GitHub does NOT collapse runs of spaces

    slugs = {slug(h) for h in heads}
    anchors = {m for m in re.findall(r"\]\(#([-\w]+)\)", text)}
    assert len(anchors) >= 5, "found %d internal anchors -- extraction misaimed" % len(anchors)
    dead = sorted(a for a in anchors if a not in slugs)
    assert not dead, "README links to headings that do not exist: %r" % dead


# --------------------------------------------------------------------------------------------------
# NEGATIVE CONTROL -- the guard has to be able to fail
# --------------------------------------------------------------------------------------------------
def test_the_guard_fires_on_an_invented_capability():
    """Feed the checker a README that names something nobody ships and require it to be caught.

    Without this, every assertion above passes equally well when the extraction has silently stopped
    matching anything -- the failure mode this whole file was written about.
    """
    surface = public_surface()
    text = _read(README) + "\n\nNew in 2.0: `quantum_recall()` re-ranks across timelines.\n"
    missing = unknown_capabilities(text, surface)
    assert "quantum_recall" in missing, (
        "the guard did NOT catch an invented capability -- it is measuring nothing")


def test_the_guard_fires_on_an_invented_attribute_form():
    """The `m.name` shape has to be caught too, not just `name(`."""
    surface = public_surface()
    text = _read(README) + "\n\nCall `m.telepathy` for the rest.\n"
    assert "telepathy" in unknown_capabilities(text, surface)


def test_the_guard_passes_a_real_capability():
    """The other half of the control: a name that DOES exist must not be flagged, or the guard is
    just a tripwire that fires on everything and tells you nothing."""
    surface = public_surface()
    assert unknown_capabilities("`provenance()` and `m.revert` and `erasure_certificate()`", surface) == []
