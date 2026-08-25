"""Check every load-bearing claim in this README against the PUBLISHED package.

Run it yourself — that is the point:

    python claims_audit.py                 # downloads the current wheel from PyPI and audits it
    python claims_audit.py --version 1.24.0
    python claims_audit.py --local         # audit the working tree instead

It downloads the wheel, unpacks it into a temp directory, and runs each claim as an independent
check against THAT artifact, never against the working copy. Each line prints PASS / FAIL /
NOT-TESTABLE-HERE with the raw evidence, so the output can be read rather than trusted.

Why this file exists: on 2026-07-20 the README said erasure leaves a signed receipt. Installing the
published wheel and testing that one sentence took ten minutes and found that plain `forget()` left
no receipt at all — the record was gone, the bytes were gone, and the store's own `verify_writes()`
then reported the deletion as `out-of-band`, i.e. accused its own API call of tampering (fixed in
1.24.0). One claim tested, one claim broken. This audits the rest.

Claims about OTHER systems (the comparison table) are listed and explicitly marked untestable here,
because verifying them means running those systems, not this one. They are not silently counted as
passes.
"""
import argparse
import collections
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

PKG_ENV = "INSPEXIMUS_AUDIT_PKG"


def _load():
    """Import inspeximus from the artifact under audit (set by the parent process)."""
    p = os.environ.get(PKG_ENV)
    if p and p not in sys.path:
        sys.path.insert(0, p)
    import inspeximus as inspeximus
    from inspeximus.core import Inspeximus
    return inspeximus, Inspeximus


def _store(tmp_name, keyed=True):
    inspeximus, Inspeximus = _load()
    from inspeximus.core import regex_extractor
    d = pathlib.Path(tempfile.mkdtemp(prefix=f"audit_{tmp_name}_"))
    m = Inspeximus(path=str(d / "store.jsonl"))
    if keyed:
        m.extractor = regex_extractor
        m.echo_guard = True
    return m, d


# --------------------------------------------------------------------------- checks
# Each returns (ok, evidence). Keep them independent: one store each, no shared state.

def c_zero_deps():
    """README: 'zero-dependency single file'.

    This read installed METADATA only. In a SOURCE checkout that glob matches nothing, so `requires` was
    the empty list, the check reported "mandatory requirements=none" and passed -- and it would have gone
    on passing if a hard dependency were added to pyproject tomorrow. It runs on every matrix leg and as
    the pre-publish gate, and exactly one of those legs installs a wheel, so for the rest it was a check
    that could not fail about the claim on the first line of the README.

    Now: installed METADATA when there IS one, the DECLARED dependencies in pyproject.toml otherwise, and
    a hard FAIL if neither can be read -- because "I found no dependencies" and "I could not look" produce
    the same empty list."""
    inspeximus, _ = _load()
    root = pathlib.Path(inspeximus.__file__).resolve().parent
    meta = list(root.parent.glob("*.dist-info/METADATA"))
    requires, source = [], None
    if meta:
        source = f"installed METADATA ({meta[0].parent.name})"
        for ln in meta[0].read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("Requires-Dist:") and "extra ==" not in ln:
                requires.append(ln.split(":", 1)[1].strip())
    else:
        pyproject = root.parent / "pyproject.toml"
        if pyproject.exists():
            source = "pyproject.toml [project] dependencies"
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
            if block:
                requires = [d.strip() for d in re.findall(r'"([^"]+)"', block.group(1))]
        if source is None:
            return False, ("neither an installed METADATA nor a pyproject.toml could be read, so the "
                           "zero-dependency claim was not checked -- which is not the same as verified")
    core = root / "core.py"
    return (not requires), (f"mandatory requirements={requires or 'none'} (read from {source}); "
                            f"core file={core.stat().st_size//1024} KB")


def c_no_llm_on_write():
    """README: 'no LLM on write — deterministic'. Enforced by making the network unusable."""
    real = socket.socket

    class _Blocked(socket.socket):
        def __init__(self, *a, **k):
            raise AssertionError("write path opened a socket")

    socket.socket = _Blocked
    try:
        m, _ = _store("nollm")
        for t in ("My address is Unit 3B.", "My manager is Rachel Tseng."):
            m.remember(t)
        hits = m.recall("address", k=2, mode="lexical", reinforce=False)
        return True, f"{len(m.items)} writes + 1 recall with sockets disabled; recall returned {len(hits or [])}"
    except AssertionError as e:
        return False, str(e)
    finally:
        socket.socket = real


def c_supersession():
    """README: 'corrections stick — supersession + echo_guard'."""
    m, _ = _store("sup")
    m.remember("My address is 742 Birchwood Lane, Unit 3B.")
    m.remember("My address is 742 Birchwood Lane, Unit 4A.")
    rows = [(r["status"], r["text"]) for r in m.items if r.get("key")]
    act = [t for s, t in rows if s == "active" and "address" in t.lower()]
    sup = [t for s, t in rows if s == "superseded"]
    ok = len(act) == 1 and "4A" in act[0] and any("3B" in t for t in sup)
    return ok, f"active={act}; superseded={sup}"


def c_revert():
    """README: 'revert(key) — restore the predecessor'. Without being told the old value."""
    m, _ = _store("rev")
    m.remember("My address is 742 Birchwood Lane, Unit 3B.")
    m.remember("My address is 742 Birchwood Lane, Unit 4A.")
    key = [r["key"] for r in m.items if r.get("key") and "address" in r["key"]][0]
    out = m.revert(key)
    active = [r["text"] for r in m.items if r.get("key") == key and r["status"] == "active"]
    ok = bool(out.get("ok")) and any("3B" in t for t in active)
    return ok, f"revert()->{json.dumps(out, default=str)[:120]}; active now={active}"


def c_forget_receipt():
    """README (1.24.0): 'every deletion path leaves a receipt'."""
    m, d = _store("fgt")
    keep = m.remember("My manager is Rachel Tseng.")
    drop = m.remember("My employee ID is MCG-20250115-47.")
    m._save(force=True)
    res = m.forget(ids=drop, request_id="audit", basis="claims_audit")
    m._save(force=True)
    disk = (d / "store.jsonl").read_text(encoding="utf-8", errors="replace")
    toms = [t for t in getattr(m, "_tombstones", []) if t.get("memory_id") == drop]
    ok = ("MCG-20250115-47" not in disk) and len(toms) == 1 and any(r["id"] == keep for r in m.items)
    return ok, f"forget()->{res}; bytes_gone={'MCG-20250115-47' not in disk}; tombstones={len(toms)}"


def c_verify_after_forget():
    """README: deletion is 'accounted for' — the audit must not call it tampering."""
    m, _ = _store("vfy")
    i = m.remember("My employee ID is MCG-20250115-47.")
    m._save(force=True)
    m.forget(ids=i)
    v = m.verify_writes()
    problems = v[1] if isinstance(v, tuple) else (v.get("problems") if isinstance(v, dict) else v)
    bad = [p for p in (problems or []) if "out-of-band" in str(p)]
    return (not bad), f"verify_writes()->{v}"


def c_tamper_detected():
    """README: 'tamper-evident write chain' — editing a stored record must be caught.

    NOTE, and the first version of this check got it wrong: write receipts are OPT-IN
    (`Inspeximus(..., receipts=True)`). Without them there is no chain to compare against, so
    verify_writes() returns clean and the check FAILED against correct code. The claim is about the
    receipt chain, so the store under test must have it enabled — auditing a feature with the feature
    switched off measures nothing.
    """
    inspeximus, Inspeximus = _load()
    d = pathlib.Path(tempfile.mkdtemp(prefix="audit_tamper_"))
    m = Inspeximus(path=str(d / "store.jsonl"), receipts=True)
    i = m.remember("My salary is 74500.")
    m._save(force=True)
    clean = m.verify_writes()
    for r in m.items:
        if r["id"] == i:
            r["text"] = "My salary is 999999."
    v = m.verify_writes()
    problems = v[1] if isinstance(v, tuple) else (v.get("problems") if isinstance(v, dict) else v)
    return bool(problems), f"before edit={str(clean)[:40]}; after silent edit->{str(v)[:150]}"


def c_determinism():
    """README: 'deterministic by construction' — same input, same state, any machine."""
    hashes = []
    for n in range(2):
        m, _ = _store(f"det{n}")
        for t in ("My address is Unit 3B.", "My manager is Rachel Tseng.", "My address is Unit 4A."):
            m.remember(t)
        hashes.append(hashlib.sha256(json.dumps(
            sorted((r.get("text", ""), r.get("status", ""), r.get("key") or "") for r in m.items),
            ensure_ascii=False).encode()).hexdigest())
    return hashes[0] == hashes[1], f"runA={hashes[0][:24]} runB={hashes[1][:24]}"


def c_trusted_only_fails_closed():
    """CHANGELOG 1.19.0: 'recall(trusted_only=True) fails CLOSED with no trust_seeds'."""
    m, _ = _store("trust")
    m.remember("The bank account is 123.")
    hits = m.recall("bank account", k=3, mode="lexical", reinforce=False, trusted_only=True)
    return len(hits or []) == 0, f"no trust_seeds -> trusted_only returned {len(hits or [])} hits"


def c_tenant_isolation():
    """README: 'tenant isolation' — one tenant must not recall another's records."""
    inspeximus, Inspeximus = _load()
    d = pathlib.Path(tempfile.mkdtemp(prefix="audit_tenant_"))
    base = Inspeximus(path=str(d / "s.jsonl"))
    try:
        # the API is for_tenant(); a first pass guessed tenant_view() and reported SKIP on a feature
        # that is present — a wrong method name reads exactly like a missing feature
        a = base.for_tenant("acme") if hasattr(base, "for_tenant") else None
        b = base.for_tenant("globex") if hasattr(base, "for_tenant") else None
        if a is None:
            # A README-asserted capability that is ABSENT is a failed claim, not an inapplicable check.
            # It reads as SKIP only when auditing a historical release that predates it -- main() decides
            # that, because only main() knows which artifact is under audit.
            return None, "MISSING: no for_tenant() on this build"
        a.remember("Acme's launch code is ALPHA.")
        b.remember("Globex's launch code is BETA.")
        leak = [h.get("text", "") for h in (b.recall("launch code", k=5, mode="lexical", reinforce=False) or [])
                if "ALPHA" in h.get("text", "")]
        return not leak, f"globex recall saw acme rows: {leak or 'none'}"
    except Exception as e:
        return False, f"raised {type(e).__name__}: {e}"


def c_witness():
    """README: 'witness()' — a digest over the store's state."""
    m, _ = _store("wit")
    m.remember("A fact.")
    w = m.witness()
    ok = isinstance(w, dict) and bool(w.get("digest"))
    return ok, json.dumps(w, default=str)[:150]


def c_pii_sweep():
    """README: 'forget_pii — data-minimisation sweep over tagged records'."""
    m, _ = _store("pii")
    try:
        m.remember("Contact me at rasto@example.com.", pii=["email"])
        m.remember("My manager is Rachel Tseng.")
        before = len(m.items)
        res = m.forget_pii(types=["email"])
        gone = not any("example.com" in r.get("text", "") for r in m.items)
        return (gone and before > len(m.items)), f"forget_pii()->{res}; email row gone={gone}"
    except Exception as e:
        return False, f"raised {type(e).__name__}: {e}"


def c_mcp_server_present():
    """README: 'an MCP server so any agent can use it as memory'."""
    inspeximus, _ = _load()
    root = pathlib.Path(inspeximus.__file__).resolve().parent
    cands = list(root.glob("*mcp*.py")) + list(root.parent.glob("*mcp*.py"))
    return bool(cands), f"module(s): {[c.name for c in cands] or 'none found in the wheel'}"


CHECKS = [
    ("zero dependencies", c_zero_deps),
    ("no LLM / no network on the write path", c_no_llm_on_write),
    ("corrections supersede the predecessor", c_supersession),
    ("revert(key) restores the predecessor unaided", c_revert),
    ("every deletion leaves a receipt (1.24.0)", c_forget_receipt),
    ("a deletion is not reported as tampering", c_verify_after_forget),
    ("a silent edit IS reported as tampering", c_tamper_detected),
    ("deterministic: same writes, same state", c_determinism),
    ("trusted_only fails closed without trust seeds", c_trusted_only_fails_closed),
    ("tenant isolation on recall", c_tenant_isolation),
    ("witness() returns a state digest", c_witness),
    ("forget_pii sweeps tagged records", c_pii_sweep),
    ("an MCP server ships in the package", c_mcp_server_present),
]

# Claims that CANNOT be settled by running this package. Listed so they are never silently
# counted as passing — verifying them means running the other systems, not this one.
NOT_TESTABLE_HERE = [
    "mem0 keeps the deleted value in its SQLite history table",
    "Zep/Graphiti retains the invalidated edge",
    "Letta has an engine-level checkpoint-undo (undo_checkpoint_block over BlockHistory), not surfaced as a first-class recall-integrity op",
    "revert-to-predecessor is rare: mem0 and Graphiti expose none; Letta has an undocumented service-layer undo",
    "secure erasure at rest (needs an encrypted store + key destruction)",
]


# =========================================================================== #
#  PUBLISHED-NUMBER AUDIT                                                     #
# =========================================================================== #
# The checks above answer "does the code do what the prose says". This half answers a different
# question the first half cannot see: "does every NUMBER we print have a command that reproduces it".
#
# Why it exists. Our own CHANGELOG carried the headline retrieval pair (recall@25 0.783 / 0.648) marked
# "reported, not independently reproducible from this repo" -- a number a reader cannot re-run is not
# evidence. The 2026-08-01 audit that added this file then found the failure was not one number but a
# class: 31 receipt paths across README/docs pointed at `inspeximus/probes/...` when the probes live at
# `probes/...` (a CHANGELOG entry had already "fixed" two of them and left the other 31); five internal
# anchors pointed at sections that had been moved out of the README, including the one advertising "the
# measured integrity number below"; the MCP tool count was published as 30, 15 and 56 on three surfaces
# at once (56 is right); and a whole README section documented two files that are not in this repository.
#
# So the rule is now mechanical rather than remembered: every numeric token on the reader-facing surface
# must be REGISTERED, either as a quantitative claim with a reproduction command and a status, or as a
# non-claim (a citation year, an article number, an example literal) with a reason and an exact count.
# An unregistered number fails this audit and fails CI. Adding one is meant to be inconvenient.

SURFACE = ("README.md", "docs/DEEP_DIVE.md", "MCP_LISTINGS.md", "index.html",
           # New pages join the SURFACE in the same commit that creates them. A published
           # page outside the audit is exactly the hole the tests badge sat in.
           "compare.html", "claude-code.html")

#: statuses a quantitative claim can carry.
STATUSES = (
    "REPRODUCIBLE",            # a committed command in THIS repo reproduces it, no external service
    "REPRODUCIBLE-WITH-DEPS",  # committed command, but needs a service/dataset we cannot ship
    "PENDING-HARNESS",         # a named harness is being built; do not quote it as verified yet
    "EXTERNAL",                # produced outside this repo; the text says so, with a pointer
    "WITHDRAWN",               # removed from the reader-facing surface by an audit
)

#: Numbers we KNOW are unbacked or unscoped, and that this audit does NOT enforce, because they live
#: outside the token-enforced files (see SURFACE). Listed rather than omitted: "not in the table" and "not a
#: problem" are different statements, and only one of them is true here. Each is a standing invitation
#: to either scope it, wire it to a harness, or delete it -- and none of them may be promoted onto the
#: reader-facing surface while it says PENDING-HARNESS.
UNENFORCED_NOTES = [
    ("inspeximus/core.py — `recall_iterative` docstring",
     "0.057 -> 0.186 (3.3x)",
     "PENDING-HARNESS",
     "Quoted with no scope. That ratio is n=70 over the THREE HARDEST LoCoMo conversations; across the "
     "full benchmark it is 0.145 -> 0.297 (2.05x), n=276, all ten conversations. A flattering subset "
     "ratio published without the subset is the exact defect this audit exists to find, and neither "
     "figure reproduces from a clean checkout (same LOCOMO dataset blocker as readme-locomo-headline). "
     "Scope it to the subset AND give the full-benchmark pair, or drop it, once a harness lands."),
    ("docs / docstrings — the recall tie policy",
     "n/a (a stated policy, not a figure)",
     "PENDING-HARNESS",
     "'equal relevance => newest first' is measured to FAIL in hybrid and auto modes: RRF gives "
     "equivalent records distinct fused scores, so the tie-break never fires and top-1 is the OLDEST. "
     "Anywhere the policy is asserted unconditionally it needs scoping to the modes where it holds."),
    ("bench/ — MemoryAgentBench Conflict Resolution",
     "any CR score",
     "PENDING-HARNESS",
     "Our own gate killed the 'supersession wins CR' headline: a naive keep-all store ties us (97 vs 96, "
     "and 85 vs 87 on the faithful re-run), and a 6k-vs-32k context discrepancy in bench/README.md is "
     "still open. No CR number may go onto the reader-facing surface until that is resolved."),
    ("index.html / MCP_LISTINGS.md / README.md — the MCP tool count",
     "60",
     "REPRODUCIBLE",
     "No longer hypothetical. Between this audit and its rebase the server grew from 56 to 58 tools; a "
     "sibling corrected ONE of the four places that publish the count (the homepage heading) and left "
     "the homepage counter, both MCP_LISTINGS figures and the README at 56. This audit named all four "
     "on its first run after the rebase. Checked against the live @mcp.tool() count, never typed."),
    ("bench/README.md — its own committed JSON",
     "9 of 12 cells",
     "PENDING-HARNESS",
     "Reported to disagree with the JSON it is generated from. Outside this audit's token-enforced "
     "scope (README.md, MCP_LISTINGS.md, index.html), and left to the unit that owns the reconciliation "
     "rather than guessed at from here."),
    ("docs / homepage — LongMemEval end-to-end",
     "0.45, vs a 0.50 oracle ceiling and a 0.05 no-memory floor, n=20",
     "PENDING-HARNESS",
     "Landed as a pilot. No figure from it has reached README.md, MCP_LISTINGS.md or index.html, and "
     "none should until it carries its scope -- n=20, a pilot, and a band check that exits 5 by design."),
]


def _prose_number_agrees():
    """A registry row's DESCRIPTION must not contradict the tokens it registers.

    Found 2026-08-25, and it had been true for weeks. This gate reads numeric TOKENS out of the
    published files and checks each one is registered. It never read the registry's own English.
    So a row could pin `73` and describe it as "The MCP server exposes 73 tools", and pass -- twice,
    in two different rows -- while README's documentation table said "all 68" and BOTH the
    og:description and twitter:description of claude-code.html said "68 tools". That is the text a
    search engine and a link preview show, and it was wrong in the one place nobody re-reads.

    The server has 73 tool defs. Three different wrong numbers were sitting beside the right one.

    The rule: every integer in a row's `claim` text that looks like a quantity must appear among that
    row's registered tokens. Years, section numbers and small ordinals are excluded, because a
    description legitimately says "Article 12" or "2026" without registering it.
    """
    # Excluded on purpose: confidence levels, years, article/section numbers, and the
    # two-digit fragments that fall out of dates and semver ("2026-08-01" -> 08, 01; "2.0.11" -> 2.0).
    # A description legitimately says "Article 12" or "mem0 2.0.11" without registering either.
    # Reviewed once, each with the reason it is context rather than a claim. An allowlist keyed by
    # (row id, number) rather than a blanket rule, so a NEW disagreement still fails and these four
    # cannot quietly cover it.
    CONTEXT_OK = {
        ("readme-locomo-headline", "1536"): "the sample size behind the recall figure, not a claim",
        ("readme-time-gap-movement", "80"): "questions per conversation; the claim is 64-83 of 320",
        ("readme-session-digest-cost", "2,606"): "the fixture size; the claim is the 7 ms",
        ("readme-supersession-8of8-withdrawn", "24"): "the denominator of a WITHDRAWN figure",
    }
    out = []
    skip = {"95", "2024", "2025", "2026", "2027", "12", "17"}
    VERSIONISH = re.compile(r"\d+\.\d+\.\d+|\d{4}-\d{2}-\d{2}|v?\d+\.\d+")
    for c in NUMBER_CLAIMS:
        toks = {t.replace(",", "").rstrip(".") for t in c["tokens"]}
        masked = VERSIONISH.sub(" ", c["claim"])
        for n in re.findall(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w%])", masked):
            bare = n.replace(",", "").rstrip(".")
            if bare in skip or len(bare) < 2:
                continue
            if (c["id"], n) in CONTEXT_OK or (c["id"], bare) in CONTEXT_OK:
                continue
            if bare not in toks and not any(bare in t or t in bare for t in toks):
                out.append(("PROSE-DISAGREES", c["file"],
                            f"claim {c['id']!r} describes {n!r} but registers {sorted(toks)}"))
    return out


def _c(id, file, tokens, pin, claim, status, command="", note=""):
    assert status in STATUSES, status
    return {"id": id, "file": file, "tokens": tuple(tokens), "pin": pin,
            "claim": claim, "status": status, "command": command, "note": note}


#: Quantitative claims. `pin` is an exact substring that MUST still be in the file -- a row whose pin is
#: gone describes nothing, so the registry cannot quietly outlive the sentence it audits.
#: The one command behind every echo-resistance figure. Named once so eleven rows cannot drift.
CMD = "python ramr_echo_resistance_backends.py  # RAMR repo"


NUMBER_CLAIMS = [
    # ---------------------------------------------------------------- README.md
    # ---- README.md (the short one; the long-form numbers live in docs/DEEP_DIVE.md) ----
    # ONE ROW PER LINE. The audit requires a claim's pin to be on the same source line as each token
    # it owns, which is correct: a number is claimed by the sentence it appears in. A four-row table
    # therefore needs four rows here, not one pinned to its first line.
    # ---- README.md "Check us without trusting us" ----
    # No rows here on purpose. The section quotes ONE line of this audit's output and no counts:
    # quoting the totals inside a file the audit reads is a fixed point that moves on every doc
    # edit, and the first draft of that section published stale figures because of it. The command
    # is the claim; the numbers belong to the run, not to the README.

    # ---- index.html: the 2.5.0 section and the corrected echo caveat ----
    # One row per LINE again: the audit requires the pin on the same source line as each token, which
    # is why a five-line caveat needs five pins and not one.
    _c("site-echo-n", "index.html", ["30"],
       "own native configuration (n=30)",
       "Sample size for the native-config echo run", "REPRODUCIBLE-WITH-DEPS",
       "python ramr_echo_resistance_backends.py  # RAMR repo"),
    # The fairness correction on the Graphiti row: our own raw output records ZERO echo-attributable
    # flips out of 26 corrections that reached the graph, so the 13.3% in the table is pre-echo
    # extraction misses and not a failure of their defense. Understating a competitor's mechanism on
    # our own front page is the same defect as overstating ours, and it runs in both directions.
    _c("readme-graphiti-13-3-decomposed", "README.md", ["13.3"],
       "The 13.3% above is four",
       "The Graphiti row's raw resurrection decomposed: four pre-echo extraction misses",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("readme-graphiti-decomposition", "README.md", ["0", "26"],
       "echo_attributable_flips: 0",
       "Graphiti's bi-temporal invalidation held 26/26 corrections that were extracted pre-echo; "
       "its 13.3% raw resurrection is four extraction misses, not echo failures",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("readme-graphiti-echo-zero", "README.md", ["0"],
       "Graphiti scores **0%**, the same as us",
       "On echo-attributable resurrection specifically, Graphiti scores 0% -- the separator is whether "
       "the supersession link is recorded at write time, not which vendor recorded it",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("site-echo-row", "index.html", ["0", "13.3", "46.7"],
       "inspeximus 0%, Graphiti 13.3%, mem0 46.7%",
       "Corrected-fact resurrection per system on their native configs",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo",
       "Replaced a STALE caveat claiming all three tie on this cell; the later run separates them."),
    _c("site-echo-control", "index.html", ["100"],
       "guard disabled we resurrect 100% of the time",
       "The control: with our guard off we resurrect every time, so the number is the mechanism",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("site-mem0-version", "index.html", ["2.0.11", "2026"],
       "mem0 measured at 2.0.11 (2026-07)",
       "The exact competitor version and date measured, stated rather than implied as current",
       "REPRODUCIBLE", "curl -s https://pypi.org/pypi/mem0ai/json"),
    _c("site-source-coverage-populated", "index.html", ["98.3"],
       "98.3% populated",
       "Our own store: fraction of records carrying a source field", "REPRODUCIBLE-WITH-DEPS",
       "curl -sO https://raw.githubusercontent.com/DanceNitra/agora/main/research/probes/can_we_reconcile_our_own_index.py && python can_we_reconcile_our_own_index.py"),
    _c("site-source-coverage-refetch", "index.html", ["0.01"],
       "and 0.01% re-fetchable",
       "Our own store: fraction whose source actually resolves", "REPRODUCIBLE-WITH-DEPS",
       "curl -sO https://raw.githubusercontent.com/DanceNitra/agora/main/research/probes/can_we_reconcile_our_own_index.py && python can_we_reconcile_our_own_index.py"),
    _c("readme-mutations-survived", "README.md", ["0"],
       "killed, 0 survived**",
       "Mutation gate: zero seeded defects survived",
       "REPRODUCIBLE", "python tools/mutation_check_parallel.py"),
    _c("readme-zero-deps-badge", "README.md", ["0"],
       "badge/dependencies-0",
       "Zero required dependencies -- every requirement in the wheel is an optional extra",
       "REPRODUCIBLE", "curl -s https://pypi.org/pypi/inspeximus/json"),
    # ---- compare.html: the measurement page ----------------------------------------------------
    # Every figure here is one already registered for README.md/index.html; this page is a new
    # SURFACE for the same measurement, not a new claim. It is registered per line anyway, because a
    # published page outside the audit is the hole the tests badge sat in for months.
    _c("cmp-schema-n", "compare.html", ["30"],
       "n=30 per system, each on its own native configuration",
       "Sample size, stated in the page's own structured data so the two cannot drift",
       "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-schema-version", "compare.html", ["2026"],
       "mem0 was measured at 2.0.11 in 2026-07",
       "The competitor version and month measured, in the structured data",
       "REPRODUCIBLE", "curl -s https://pypi.org/pypi/mem0ai/json"),
    _c("cmp-n", "compare.html", ["30"],
       "n = 30 per system",
       "Trials per system", "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-ours", "compare.html", ["100", "0"],
       "<tr class=\"us\"><td>inspeximus</td>",
       "inspeximus keeps the correction 100% of the time and resurrects the old value 0%",
       "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-graphiti", "compare.html", ["0", "86.7", "13.3", "95", "3.3", "26.7"],
       "<tr><td>Graphiti 0.x",
       "Graphiti keeps the correction 86.7%; resurrection 13.3%, 95% CI [3.3, 26.7]",
       "REPRODUCIBLE-WITH-DEPS", CMD,
       "The bare 0 on this line is the MAJOR VERSION in \"Graphiti 0.x\", not a measurement. It is "
       "listed rather than excused, because declaring \"0\" a non-claim file-wide would also excuse "
       "the two real zeros this page publishes."),
    _c("cmp-mem0", "compare.html", ["53.3", "46.7", "95", "30.0", "63.3"],
       "<tr><td>mem0 2.0.11",
       "mem0 2.0.11 keeps the correction 53.3%; resurrection 46.7%, 95% CI [30.0, 63.3]",
       "REPRODUCIBLE-WITH-DEPS", CMD,
       "Version-stamped deliberately: mem0 is on 2.0.18 and we have NOT re-run it."),
    _c("cmp-control", "compare.html", ["0"],
       "&mdash; guard disabled",
       "The control: with our own guard off, we keep the correction 0% of the time",
       "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-scope-version", "compare.html", ["2026"],
       "(2026-07) and has <b>not</b> been re-run since",
       "The month the competitor figure was measured, said in prose next to the claim",
       "REPRODUCIBLE", "curl -s https://pypi.org/pypi/mem0ai/json"),
    _c("cmp-trials-and-guard", "compare.html", ["30", "0"],
       "a 30-trial proportion is not a point",
       "Why intervals are shown; and our 0% with the guard on",
       "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-guard-off", "compare.html", ["100"],
       "guard on and 100% with it off",
       "The control restated: guard off resurrects 100% of the time",
       "REPRODUCIBLE-WITH-DEPS", CMD),
    _c("cmp-faq-hundred", "compare.html", ["100"],
       "Isn&rsquo;t 100% just a benchmark you designed to win?",
       "The objection names our own headline number", "REPRODUCIBLE-WITH-DEPS", CMD),

    # ---- claude-code.html: the MCP setup page ---------------------------------------------------
    _c("cc-tool-count", "claude-code.html", ["73"],
       "<h2>73 tools any MCP host can call</h2>",
       "The MCP server exposes 73 tools", "REPRODUCIBLE", "python claims_audit.py --numbers",
       "Checked against the live @mcp.tool() count by _live_consistency(), not read from here."),
    _c("readme-echo-trials", "README.md", ["30"],
       "same task, same 30 trials",
       "Sample size: 30 trials per system",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("readme-echo-ours", "README.md", ["100", "0"],
       "| **inspeximus** | **100%** | **0%** |",
       "inspeximus keeps a corrected fact 100% of the time; it never resurrects the old value",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("readme-echo-graphiti", "README.md", ["0", "86.7", "13.3", "95", "3.3", "26.7"],
       "| Graphiti 0.x (Neo4j + OpenAI) |",
       "Graphiti keeps the correction 86.7% of the time; resurrection 13.3%, 95% CI [3.3, 26.7]",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo",
       "Measured on the vendor's own native config (Neo4j + OpenAI), n=30."),
    _c("readme-echo-mem0", "README.md", ["2.0.11", "53.3", "46.7", "95", "30.0", "63.3"],
       "| mem0 2.0.11 (OpenAI native) |",
       "mem0 2.0.11 keeps the correction 53.3%; resurrection 46.7%, 95% CI [30.0, 63.3]",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo",
       "Version-stamped on purpose: mem0 is on 2.0.18 as of 2026-08-11 and we have NOT re-run it."),
    _c("readme-echo-control", "README.md", ["0"],
       "| inspeximus, guard disabled | 0% |",
       "The control: with the guard off we score zero, so the number is the mechanism",
       "REPRODUCIBLE-WITH-DEPS", "python ramr_echo_resistance_backends.py  # RAMR repo"),
    _c("readme-echo-n-and-version", "README.md", ["30", "2.0.11", "2026", "2.0.18"],
       "n = 30 per system",
       "Sample size per system, and the exact competitor version measured",
       "REPRODUCIBLE", "curl -s https://pypi.org/pypi/mem0ai/json"),
    _c("readme-mcp-tool-count", "README.md", ["73"],
       "**73 tools**",
       "The MCP server exposes 73 tools", "REPRODUCIBLE", "python claims_audit.py --numbers",
       "Checked against the live @mcp.tool() count by _live_consistency(), not read from here."),
    _c("readme-own-source-coverage", "README.md", ["98.3", "0.01"],
       "98.3% populated and 0.01% re-fetchable",
       "Our own production store: source populated vs actually re-checkable",
       "REPRODUCIBLE-WITH-DEPS",
       "curl -sO https://raw.githubusercontent.com/DanceNitra/agora/main/research/probes/can_we_reconcile_our_own_index.py && python can_we_reconcile_our_own_index.py",
       "Published as our own failure, not a product claim. 210,499 records across ten stores."),
    _c("readme-adapter-conformance", "README.md", ["10", "13", "3"],
       "**10 of 13 verified against current upstream, 3 recorded broken**",
       "Framework adapters: 10 of 13 verified against current upstream, 3 recorded broken",
       "REPRODUCIBLE", "python claims_audit.py --numbers",
       "Read from docs/integration_conformance.json by _live_consistency(), which now checks BOTH "
       "index.html and README.md -- a second copy of a number is a second place for it to go stale. "
       "The other 12 in this file is the EU AI Act article number and stays a declared non-claim; the counts moved 9/12 -> 10/13 when the llm-errata adapter landed, and this line is why the drift surfaced instead of shipping; "
       "COUNT-DRIFT caught the collision the moment this line was added, which is the whole point."),
    _c("readme-tests-and-mutations", "README.md", ["2,600", "175"],
       "**2,600+ tests**",
       "Suite size, and the mutation gate that makes it evidence: 175 seeded, 175 killed",
       "REPRODUCIBLE", "python tools/mutation_check_parallel.py"),
    _c("readme-erasure-fanout-hero", "docs/DEEP_DIVE.md", ["0.17", "1.00"],
       "soft delete scores 0.17 and names the five leaking stores",
       "A soft delete leaves the value recoverable in 5 of 6 stores (0.17); a wired hard delete scores 1.00",
       "REPRODUCIBLE", "python probes/forget_verification_bench.py"),
    _c("readme-erasure-fanout-table", "docs/DEEP_DIVE.md", ["1.00", "0.17"],
       "a wired hard delete scores **1.00** with a verifying signed receipt",
       "Same six-store fan-out measurement, restated in the four-operations table",
       "REPRODUCIBLE", "python probes/forget_verification_bench.py",
       "Replaced a 'measured 15/15 on a verified-forgetting severe-test' for which no artifact in this "
       "repository produces a 15/15 of anything. The bench that DOES exist scores 0.17 / 1.00 over six "
       "stores, so the sentence now cites the number the committed code prints."),
    _c("readme-vault-hero", "docs/DEEP_DIVE.md", ["10,000"],
       "run it daily over a private ~10,000-note vault",
       "inspeximus has run daily over a ~10,000-note vault",
       "EXTERNAL", "",
       "Our own private Obsidian vault. There is no command; the text now says so instead of implying "
       "the reader could check it."),
    _c("readme-vault-contradictions", "docs/DEEP_DIVE.md", ["10,000"],
       "runs in production over the 10,000-note vault",
       "Contradiction detection runs in production over the ~10,000-note vault",
       "EXTERNAL", "", "Same private deployment as the hero line."),
    _c("readme-audit-summary", "docs/DEEP_DIVE.md", ["13", "0", "5"],
       "13 passed · 0 FAILED · 0 skipped · 5 not testable here",
       "The example claims_audit run: 13 checks pass, 5 are not testable from this package",
       "REPRODUCIBLE", "python claims_audit.py --local",
       "Self-referential, so it is checked against len(CHECKS) and len(NOT_TESTABLE_HERE) rather than "
       "trusted. The block used to name inspeximus-1.24.1 while the package was at 1.89.0; the version "
       "line was dropped rather than pinned, because it would go stale on every release."),
    _c("readme-memops-scenarios", "docs/DEEP_DIVE.md", ["24", "50"],
       "long-context scenarios (24 scenarios, ~50 sessions each)",
       "MemOps: 24 long-context scenarios, ~50 sessions each",
       "EXTERNAL", "", "Harness lives in the Agora repo (agora_output/lab/memops), already linked in place."),
    _c("readme-memops-cost", "docs/DEEP_DIVE.md", ["519", "917", "606", "24"],
       "**519–917 s of LLM extraction** (median 606 s, n=24)",
       "mem0's default pipeline spends 519-917 s (median 606) of LLM extraction per MemOps scenario",
       "EXTERNAL", "", "Agora MemOps harness; needs mem0 + an LLM budget, so it cannot ship here."),
    _c("readme-memops-acc-ours", "docs/DEEP_DIVE.md", ["0.593"],
       "indistinguishable** — inspeximus 0.593",
       "MemOps answer accuracy: inspeximus 0.593", "EXTERNAL", ""),
    _c("readme-memops-acc-others", "docs/DEEP_DIVE.md", ["0.592", "0.544", "2"],
       "a naive keep-all store 0.592, mem0 0.544",
       "MemOps answer accuracy: keep-all 0.592, mem0 0.544; ~2% of mem0 extractions failed to parse",
       "EXTERNAL", ""),
    _c("readme-locomo-denominator", "docs/DEEP_DIVE.md", ["1536"],
       "n=1536), with the built-in tuned recipe",
       "The LOCOMO question denominator behind the retrieval pair",
       "REPRODUCIBLE-WITH-DEPS", "python benchmarks/locomo/run.py --subset full --retrieval-only"),
    _c("readme-locomo-headline", "docs/DEEP_DIVE.md", ["25", "0.83", "0.70"],
       "retrieval-recall@25 is 0.83** (a supporting turn is retrieved) / **0.70**",
       "LOCOMO retrieval-recall@25 = 0.83 (any evidence turn) / 0.70 (all), n=1536, reinforce=False",
       "REPRODUCIBLE-WITH-DEPS", "python benchmarks/locomo/run.py --subset full --retrieval-only",
       "This row was PENDING-HARNESS for the whole of this audit, and it is the reason that status "
       "exists. The harness landed as benchmarks/locomo/ and the row moved WITHOUT the number having "
       "been re-asserted in the meantime. Verified here against the committed result "
       "benchmarks/locomo/results/full_retrieval.json rather than against the prose: recall_any 0.8262 "
       "/ recall_all 0.6986 on the published 1536-question denominator, pinned at k=25, mode=hybrid, "
       "prefer=speaker, reinforce=false. WITH-DEPS because LOCOMO is not ours to redistribute -- the "
       "command needs locomo10.json downloaded (sha256 pinned in config.json), though the committed "
       "result is readable without it."),
    _c("readme-locomo-command", "docs/DEEP_DIVE.md", ["0.83", "0.70"],
       "--retrieval-only   # ~0.83 / 0.70, no model calls",
       "The copy-paste command with its expected output inline",
       "REPRODUCIBLE-WITH-DEPS", "python benchmarks/locomo/run.py --subset full --retrieval-only"),
    _c("readme-locomo-old-pair", "docs/DEEP_DIVE.md", ["0.7839", "0.6484", "0.783", "0.648", "1536"],
       "0.7839 / 0.6484 against the published 0.783 / 0.648, on the identical 1536-question denominator",
       "The OLD published pair reproduces exactly at its own operating point (reinforce=True)",
       "REPRODUCIBLE-WITH-DEPS", "python benchmarks/locomo/run.py --subset full --retrieval-only",
       "The pair this audit opened on. It was never wrong -- it was measured with recall()'s "
       "reinforce=True default, which mutates value/last_access, so each benchmark query was answered "
       "by a store the previous queries had modified and the score depended on question order. Pinning "
       "reinforce=False makes the run deterministic and scores 4-5 points HIGHER. A number that moves "
       "when you fix the instrument is exactly what an unreproducible number hides."),
    _c("readme-locomo-caveat-dates", "docs/DEEP_DIVE.md", ["2026", "0.78", "0.65"],
       "**The old pair was 0.78 / 0.65, and it reproduces exactly**",
       "The superseded pair, quoted inside the note that discharges its caveat",
       "REPRODUCIBLE-WITH-DEPS", "python benchmarks/locomo/run.py --subset full --retrieval-only"),
    _c("readme-locomo-n", "docs/DEEP_DIVE.md", ["1536,"],
       "on one LoCoMo config (n=1536, deterministic",
       "The LoCoMo config size behind recall_any@1", "PENDING-HARNESS",
       "python probes/retrieval_recall_locomo.py --k 25"),
    _c("readme-competitor-judges", "docs/DEEP_DIVE.md", ["66.9", "71.2"],
       "mem0 reports 66.9% and Zep 71.2% under their own judges",
       "mem0 and Zep's self-reported LLM-judged QA scores", "EXTERNAL", "",
       "Other projects' published numbers, cited as not comparable across harnesses -- which is the "
       "point the sentence makes."),
    # 1.90.0 (#8) DELETED the paragraph this row used to pin -- the 5.2% / 1,037 / 19,851 figures came
    # from the MemOps harness, which lives outside this repository and therefore could never be re-run
    # here. The sentence that carried them is gone, so the row goes with it rather than pinning nothing.
    # What replaced it is measured IN this repo and has a command, so it is a claim, not an external.
    _c("readme-time-gap-fixture", "docs/DEEP_DIVE.md", ["80"],
       "Four LOCOMO conversations, 80 questions each",
       "The time-gap measurement runs on four LOCOMO conversations, 80 questions sampled from each",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-movement", "docs/DEEP_DIVE.md", ["64", "83", "320"],
       "between 64 and 83 of 320 top-1 answers differ",
       "Reading the same untouched store twice ~2s apart moves 64-83 of 320 top-1 answers "
       "(four LOCOMO conversations, 80 questions each, reinforce=False)",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-cause", "docs/DEEP_DIVE.md", ["0", "80"],
       "at a zero gap 0 of 80 differ",
       "GAP CONTROL: 0 of 80 top-1 answers move when the two reads are not separated at all",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py",
       "Registered deliberately rather than dropped: without a zero-gap arm, 'the ranking depends on "
       "when you ask' is only an observation about two reads and names no cause."),
    _c("readme-time-gap-saturates", "docs/DEEP_DIVE.md", ["16", "80"],
       "16 of 80 move at two seconds and the same count at ten seconds",
       "The effect saturates: 16 of 80 move at a two-second gap and the same count at ten",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-tie-band", "docs/DEEP_DIVE.md", ["0.847"],
       "same score (0.847 against 0.847)",
       "Every top-1 answer that moved across the gap moved between records reported at the same "
       "score, e.g. 0.847 against 0.847",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-tie-band-share", "docs/DEEP_DIVE.md", ["100", "6"],
       "100% of them, in all six insert orders tried",
       "100% of the moved answers stayed inside a displayed tie, across all six insert orders",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-no-direction", "docs/DEEP_DIVE.md", ["0.0094"],
       "the hit@1 change runs +0.0094 to",
       "Across five randomised insertion orders the hit@1 change over the gap runs +0.0094 to -0.0219",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py"),
    _c("readme-time-gap-spread-low", "docs/DEEP_DIVE.md", ["-0.0219", "2", "5", "-0.0062"],
       "-0.0219, negative in 2 of 5. Natural conversation order alone reads -0.0062",
       "The hit@1 change is negative in only 2 of 5 randomised insert orders; natural conversation "
       "order alone reads -0.0062",
       "REPRODUCIBLE-WITH-DEPS", "python probes/recall_over_a_time_gap.py",
       "The natural-order figure is registered beside the spread on purpose: alone it reproduces to "
       "four decimals every run and reads as a systematic loss, which is the fixture (LOCOMO gold "
       "turns skew late, so gold records are newer) and not a property of the store."),
    _c("readme-fixed-instant-determinism", "docs/DEEP_DIVE.md", ["0.0000"],
       "arm (a) of the reinforce ablation measures 0.0000 on every corpus",
       "Run-to-run determinism at a fixed instant: arm (a) divergence 0.0000 on every corpus",
       "REPRODUCIBLE-WITH-DEPS", "python probes/reinforce_accuracy_ablation.py"),
    _c("readme-session-digest-fixture", "docs/DEEP_DIVE.md", ["2,606", "1.000"],
       "Measured on an 8-session, 2,606-record fixture",
       "SessionEnd digest -> SessionStart injection, 8-session / 2,606-record fixture: injection recall "
       "1.000 of a session's conclusions reach the next session",
       "REPRODUCIBLE", "python probes/session_digest_multisession.py"),
    _c("readme-session-digest-rejection", "docs/DEEP_DIVE.md", ["1.0000"],
       "**1.0000** of below-threshold items stay out",
       "Below-threshold rejection 1.0000 on the same fixture",
       "REPRODUCIBLE", "python probes/session_digest_multisession.py"),
    _c("readme-session-digest-control", "docs/DEEP_DIVE.md", ["0.2213"],
       "collapses to **0.2213**",
       "NEGATIVE CONTROL: with the salience bar removed, rejection collapses to 0.2213",
       "REPRODUCIBLE", "python probes/session_digest_multisession.py",
       "Registered deliberately rather than dropped: without it a rejection of 1.0000 cannot be told "
       "apart from a fixture that contained nothing to reject."),
    _c("readme-session-digest-cost", "docs/DEEP_DIVE.md", ["7"],
       "`close_session` costs 7 ms at that size",
       "close_session costs 7 ms on the 2,606-record fixture",
       "REPRODUCIBLE", "python probes/session_digest_multisession.py"),
    _c("readme-chain-binding", "docs/DEEP_DIVE.md", ["15", "18", "60", "2", "9", "1", "0", "8", "4"],
       "| correction chains that collapse to one record holding the final value | 2/15 |",
       "regex_extractor chain binding on benchmarks/chain_binding/ (15 chains, 18 unrelated pairs, "
       "60 prose sentences): chains collapsing to one record 2/15 -> 9/15; false binds on unrelated "
       "pairs 1/18 -> 0/18; non-declarative prose keyed 8/60 -> 4/60",
       "REPRODUCIBLE", "python benchmarks/chain_binding/probe.py",
       "The 'before' column is measured against `git show main:inspeximus/core.py` on the same fixture, "
       "not quoted from elsewhere. The false-bind row is the control: a keyer that binds everything "
       "scores a perfect 15/15 while tripping all 18 negative pairs, which is why the bind rate alone "
       "is not evidence."),
    _c("readme-ramr-echo", "docs/DEEP_DIVE.md", ["0.00", "0.57", "1.00"],
       "(keyed-without-guard 0.00, an add-based system 0.57, guard 1.00)",
       "RAMR ECHO-RESISTANCE: keyed-without-guard 0.00, add-based 0.57, echo_guard 1.00",
       "EXTERNAL", "",
       "From RAMR, a separate repository. The README presented these as if produced here; it now says "
       "where they come from AND points at this repo's own echo cell, which measures a different "
       "quantity and does NOT flatter us."),
    _c("readme-integrity-echo-cell", "docs/DEEP_DIVE.md", ["0.00", "0.05"],
       "0.00, mem0 0.05, Graphiti 0.00",
       "In-repo cross-system echo cell: resurrection rate inspeximus 0.00, mem0 0.05, Graphiti 0.00",
       "REPRODUCIBLE-WITH-DEPS", "python probes/integrity_bench_echo.py --systems inspeximus",
       "The inspeximus column runs locally and free; the mem0/Graphiti columns need OPENAI_API_KEY and a "
       "live neo4j, which is why this is WITH-DEPS rather than REPRODUCIBLE."),
    _c("readme-recall-any1", "docs/DEEP_DIVE.md", ["0.397"],
       "lands recall_any@1 at 0.397",
       "recall_any@1 = 0.397 with nomic task prefixes on one LoCoMo config",
       "PENDING-HARNESS", "python probes/retrieval_recall_locomo.py --k 1",
       "Same dataset blocker as the headline pair; already flagged in place as not reproducible here."),
    _c("readme-locomo-confound", "docs/DEEP_DIVE.md", ["0.19", "0.29"],
       "0.19→0.29 delta was contaminated",
       "A withdrawn 0.19->0.29 delta, cited as an example of a confound we found and corrected",
       "EXTERNAL", "",
       "Kept deliberately: it is a retraction, not a claim. Removing it would erase the correction."),
    _c("readme-lexical-decay", "docs/DEEP_DIVE.md", ["5", "0.94", "0.25"],
       "lexical `recall@5` decays from **0.94** (small store) to **0.25**",
       "Lexical recall@5 decays 0.94 -> 0.25 as the store grows", "EXTERNAL", "",
       "Agora Lab b4c260, cited in place. No probe in this repository reproduces it."),
    _c("readme-semantic-hold", "docs/DEEP_DIVE.md", ["0.65", "2.6"],
       "while semantic **holds at ~0.65** — ≈**2.6×** at full scale",
       "Semantic recall@5 holds ~0.65 at full scale, ~2.6x lexical", "EXTERNAL", "",
       "Agora Lab b4c260."),
    _c("readme-paraphrase", "docs/DEEP_DIVE.md", ["5", "0.86", "0.20"],
       "semantic `recall@5` is **0.86 vs 0.20** lexical",
       "On paraphrase queries semantic recall@5 is 0.86 vs 0.20 lexical", "EXTERNAL", "",
       "Agora Lab 3501f1."),
    _c("readme-hub-prune", "docs/DEEP_DIVE.md", ["20"],
       "lifts **lexical** recall ~20% only when a store is link-spammed",
       "Pruning hub notes lifts lexical recall ~20% on a link-spammed store only", "EXTERNAL", ""),
    _c("readme-consolidation-half", "docs/DEEP_DIVE.md", ["1.8"],
       "the budget shrinks** (≈1.8× at half",
       "Value-ranked consolidation beats FIFO by ~1.8x at half budget", "EXTERNAL", ""),
    _c("readme-consolidation-eighth", "docs/DEEP_DIVE.md", ["4"],
       "budget → ≈4× at one-eighth",
       "...and by ~4x at one-eighth budget", "EXTERNAL", ""),
    _c("readme-retention-cold", "docs/DEEP_DIVE.md", ["30", "2.8"],
       "At a 30% keep-budget the access-decay policy retained only **2.8%**",
       "At a 30% keep-budget, access-decay retains 2.8% of high-value/low-frequency memories",
       "EXTERNAL", "", "Agora Lab 19d802."),
    _c("readme-retention-value", "docs/DEEP_DIVE.md", ["20", "100", "64"],
       "and **20%** of total value, vs **100%** and **64%** for",
       "...20% of total value, vs 100% and 64% for the value-aware blend", "EXTERNAL", ""),
    _c("readme-retention-gap", "docs/DEEP_DIVE.md", ["3", "2.2", "7"],
       "about **3× more value kept** (the gap persists, ≈2.2× retained value, even at a 7%",
       "~3x more value kept, persisting at ~2.2x even at a 7% budget", "EXTERNAL", ""),
    _c("readme-supersession-auroc", "docs/DEEP_DIVE.md", ["0.61"],
       "scores **AUROC ~0.61**",
       "A cosine classifier separating a contradiction from a rephrase scores AUROC ~0.61",
       "REPRODUCIBLE-WITH-DEPS", "python probes/supersession_replication.py",
       "Re-run 2026-08-01: AUROC 0.613. Needs a local nomic-embed-text (Ollama) and numpy."),
    _c("readme-supersession-stale", "docs/DEEP_DIVE.md", ["42"],
       "serves the **stale value ~42% of the time**",
       "A similarity-based store serves the stale value ~42% of the time",
       "REPRODUCIBLE-WITH-DEPS", "python probes/supersession_replication.py",
       "Re-run 2026-08-01: 41.7%."),
    _c("readme-supersession-zero", "docs/DEEP_DIVE.md", ["0"],
       "to **0%**. Re-run it: `python probes/supersession_replication.py`",
       "The deterministic SRO key drives the stale-value rate to 0%",
       "REPRODUCIBLE-WITH-DEPS", "python probes/supersession_replication.py"),
    _c("readme-supersession-rerun", "docs/DEEP_DIVE.md", ["0.613", "41.7", "0.0"],
       "reproduced AUROC 0.613, stale-fact-error 41.7% under pure cosine and 0.0% under the SRO key",
       "The 2026-08-01 re-run of that probe, quoted with its date",
       "REPRODUCIBLE-WITH-DEPS", "python probes/supersession_replication.py"),
    _c("readme-supersession-8of8-withdrawn", "docs/DEEP_DIVE.md", ["0", "8"],
       "0/24, no artifact here produces an 8/8, and that figure has been withdrawn",
       "WITHDRAWN: 'severe-test 8/8' -- the probe reports 0/24 and nothing here produces an 8/8",
       "WITHDRAWN", "python probes/supersession_replication.py"),
    _c("readme-memops-parsefail", "docs/DEEP_DIVE.md", ["2"],
       "About 2% of mem0's extraction calls failed to parse",
       "~2% of mem0's MemOps extraction calls failed to parse", "EXTERNAL", ""),
    _c("readme-operating-cosine", "docs/DEEP_DIVE.md", ["42"],
       "store scores **42%** (fine on stable, but blind to",
       "Operating-point trap: a cosine top-1 store scores 42%",
       "REPRODUCIBLE-WITH-DEPS", "python probes/operating_point_memory.py",
       "Needs a local nomic-embed-text (Ollama)."),
    _c("readme-operating-recency", "docs/DEEP_DIVE.md", ["0", "8", "67"],
       "supersession — **0/8** on updated facts — and fooled by repeated lies); a **recency** store **67%**",
       "...0/8 on updated facts; a recency store scores 67%",
       "REPRODUCIBLE-WITH-DEPS", "python probes/operating_point_memory.py"),
    _c("readme-operating-poison", "docs/DEEP_DIVE.md", ["0", "8"],
       "*freshest lie* — **0/8** on poison)",
       "...and 0/8 on poison", "REPRODUCIBLE-WITH-DEPS", "python probes/operating_point_memory.py"),
    _c("readme-operating-layered", "docs/DEEP_DIVE.md", ["100"],
       "value-ranking — is **100%**, robust across all three",
       "The layered store scores 100% across all three operating points",
       "REPRODUCIBLE-WITH-DEPS", "python probes/operating_point_memory.py"),
    _c("readme-cohort-power", "docs/DEEP_DIVE.md", ["0.36"],
       "reached only ~0.36 power at realistic sample sizes",
       "Per-memory outcome attribution reaches only ~0.36 power at n-of-1", "EXTERNAL", ""),
    _c("readme-sybil-attack", "docs/DEEP_DIVE.md", ["0.9", "10"],
       "(~0.9 attack-success across 10 models",
       "Content-declared corroboration falls to a sybil at ~0.9 attack-success across 10 models",
       "REPRODUCIBLE-WITH-DEPS", "python probes/memory_defense_layer_probe.py",
       "The harness is committed; reproducing the number needs ten models and a judge, which no "
       "checkout can ship."),
    _c("readme-bedrock-directions", "docs/DEEP_DIVE.md", ["8"],
       "Checked from ~8 directions",
       "The bedrock synthesis was checked from ~8 directions", "EXTERNAL", "",
       "A count of the analytical directions taken, not a measurement. Left in because the sentence "
       "labels itself 'a synthesis over those cases, not a proof'."),
    _c("readme-mcp-tools", "docs/DEEP_DIVE.md", ["73"],
       "`inspeximus-mcp`, 73 tools",
       "The MCP server exposes 73 tools", "REPRODUCIBLE",
       'python -c "import re,pathlib;print(len(re.findall(chr(64)+chr(109)+chr(99)+chr(112)+chr(46)+'
       "'tool', pathlib.Path('inspeximus/mcp_server.py').read_text(encoding='utf-8'))))\"",
       "Checked against the live @mcp.tool() count by _live_consistency(), not by reading it here."),

    # ---------------------------------------------------------- MCP_LISTINGS.md
    _c("mcp-tool-count", "MCP_LISTINGS.md", ["73"],
       "`inspeximus-mcp`, 73 tools",
       "The MCP server exposes 73 tools", "REPRODUCIBLE",
       "python claims_audit.py --numbers",
       "Published as 30 until 2026-08-01 -- 26 short -- while the homepage said 15 in one place and 56 "
       "in another. Three surfaces, one server, no error anywhere. Now read from the code."),
    _c("mcp-tool-list", "MCP_LISTINGS.md", ["73"],
       "**Tools (73):**",
       "The enumerated tool list matches the server", "REPRODUCIBLE",
       "python claims_audit.py --numbers"),
    _c("mcp-stale-30", "MCP_LISTINGS.md", ["30", "26", "2026"],
       "It said 30 until 2026-08-01, when it was",
       "WITHDRAWN: the previous '30 tools' figure, kept as the record of the correction",
       "WITHDRAWN", "python claims_audit.py --numbers"),

    # -------------------------------------------------------------- index.html
    _c("site-mcp-tools-counter", "index.html", ["73", "0"],
       'data-count="73">0</b><span>MCP tools',
       "Homepage counter: 73 MCP tools", "REPRODUCIBLE", "python claims_audit.py --numbers",
       "Was 15. The counter renders data-count, so the figure a reader sees lives in an attribute -- "
       "which is why the scanner hoists data-count out of the tag before stripping tags."),
    _c("site-mcp-tools-heading", "index.html", ["73"],
       "73 tools any MCP host can call",
       "Homepage heading: 73 MCP tools", "REPRODUCIBLE", "python claims_audit.py --numbers"),
    _c("site-adapters", "index.html", ["9", "0"],
       'data-count="9">0</b><span>framework adapters',
       "Homepage counter: 9 framework adapters", "REPRODUCIBLE",
       "python -c \"import pathlib;print(sorted(p.stem for p in pathlib.Path('inspeximus/integrations')"
       ".glob('*.py')))\"",
       "Was 6 while the README said nine and the package ships nine agent-framework adapters "
       "(autogen, crewai, google_adk, haystack, langchain, langgraph, llamaindex, openai_agents, "
       "pydantic_ai)."),
    _c("site-integration-conformance", "index.html", ["10", "13", "3"],
       "10 of 13 verified against current upstream, 3 recorded broken",
       "10 of 13 framework adapters verified against current upstream; 3 recorded broken",
       "REPRODUCIBLE", "python tools/integration_conformance.py",
       "Read from the committed ledger docs/integration_conformance.json by _live_consistency(), not "
       "typed. The page previously said 'Drop-in for' all nine frameworks with no qualifier at all, "
       "while crewai 1.15.6, openai-agents 0.18.3 and langgraph-checkpointer 1.2.9 were recorded "
       "broken -- an unqualified capability claim contradicted by a JSON file in the same repo."),
    _c("site-zero-deps", "index.html", ["0"],
       "<b>0</b><span>runtime dependencies",
       "Homepage counter: 0 runtime dependencies", "REPRODUCIBLE", "python claims_audit.py --local",
       "This is the c_zero_deps check, which reads installed METADATA or, failing that, the declared "
       "pyproject dependencies -- and hard-fails if it can read neither."),
    _c("site-revert-bench", "index.html", ["0.75", "0.20", "0.00", "20", "95"],
       "over 20 trials, with 95% Wilson intervals",
       "Cross-system revert success over n=20: inspeximus 0.75, mem0 0.20, Graphiti 0.00",
       "REPRODUCIBLE-WITH-DEPS", "python probes/integrity_bench_revert.py --systems inspeximus --n 20",
       "The inspeximus column runs locally; mem0 needs OPENAI_API_KEY and Graphiti a live neo4j. "
       "Methodology and CIs: probes/INTEGRITY_BENCHMARK.md."),
    _c("site-revert-counter", "index.html", ["0.75", "0.20", "20", "0"],
       'data-count="0.75" data-decimals="2">0</b><span>revert success (vs 0.20, n=20)',
       "Homepage counter restating the revert cell", "REPRODUCIBLE-WITH-DEPS",
       "python probes/integrity_bench_revert.py --systems inspeximus --n 20"),
    _c("site-bench-inspeximus", "index.html", ["0.75"], ">0.75<",
       "Benchmark bar: inspeximus 0.75", "REPRODUCIBLE-WITH-DEPS",
       "python probes/integrity_bench_revert.py --systems inspeximus --n 20"),
    _c("site-bench-mem0", "index.html", ["0.20"], ">0.20<",
       "Benchmark bar: mem0 0.20", "REPRODUCIBLE-WITH-DEPS",
       "python probes/integrity_bench_revert.py --systems inspeximus,mem0 --n 20"),
    _c("site-bench-graphiti", "index.html", ["0.00"], ">0.00<",
       "Benchmark bar: Graphiti 0.00", "REPRODUCIBLE-WITH-DEPS",
       "python probes/integrity_bench_revert.py --systems inspeximus,graphiti --n 20"),
    # The instrument, published beside the numbers it produces. Replaying ONE store run's retrieved
    # contexts through five judges moves the inspeximus figure from 0.75 to 0.80-1.00 with nothing
    # about the store changing, so the caveat under the chart is itself a measurement and is
    # registered like one.
    _c("site-bench-judge-nondeterminism", "index.html",
       ["30", "26", "0.70", "0.75", "0.80", "0.7500", "0.05"],
       "0.75 in 26 of 30 runs",
       "The judge is not deterministic at temperature 0.0: 30 runs on byte-identical contexts give "
       "0.75 x26, 0.70 x2, 0.80 x2; mean 0.7500; the store is deterministic",
       "REPRODUCIBLE-WITH-DEPS",
       "python probes/the_judge_is_not_deterministic_at_temperature_zero.py --runs 30",
       "Needs OPENAI_API_KEY. Two runs an hour apart both returned 0.75 and that was written up as "
       "'reproduces to the digit'; a third returned 0.70. The store arm is the control: 5 runs, 1 "
       "distinct context set. B=0 in every run, so the band is abstention."),
    _c("site-bench-judge-sensitivity", "index.html", ["0.80", "1.00"],
       "the figure moves to 0.80, inside that band",
       "Judge sensitivity among comparable judges is no larger than the same judge's run-to-run band",
       "REPRODUCIBLE-WITH-DEPS",
       "python probes/does_the_headline_number_depend_on_who_judges_it.py --n 20",
       "Needs OPENAI_API_KEY. The first version of this row said 0.80-1.00 and mixed comparable with "
       "non-comparable columns: only gpt-4o-mini 0.75, gpt-5.4-nano 0.80 and gpt-5.4-mini 0.80 ran at "
       "temperature 0.0. gpt-5.5 (1.00) and gpt-5.6-luna (0.85) REFUSED temperature 0.0. The honest "
       "delta is +0.05, one case in twenty, McNemar p=1.0. B=0 in every column including the "
       "deterministic control at 1.00, so the spread is abstention, not disagreement about the revert."),
]

#: Every remaining numeric token, with an exact expected count and a reason it is not a claim.
#: The count is the guard: a NEW number under an already-registered token still fails, because the
#: total moved. Nothing here may be a measurement -- if a row needs the word "measured", it belongs
#: in NUMBER_CLAIMS with a command instead.
NON_CLAIM_TOKENS = {
    "README.md": {
        # Declared, with the reason, because a token nobody claims is not the same as a token nobody
        # looked at -- and this file was briefly outside SURFACE, where the audit passed by not reading it.
        "30": (1, "the rhetorical heading 'The 30 seconds that matter', not a quantity"),
        "01": (1, "example FILENAME 01_basics.py, not a quantity"),
        "02": (1, "example FILENAME 02_correction_and_erasure.py, not a quantity"),
        "03": (1, "example FILENAME 03_semantic_recall.py, not a quantity"),
        "06": (1, "example FILENAME 06_gdpr_erasure_receipt.py, not a quantity"),
        "5": (2, "twice, both parameters a reader changes rather than results we claim: the --n 5 "
              "argument in the offered local-judge command, and k=5 in the next-five-minutes recall "
              "snippet"),
        "0": (2, "Python list indices [0] in the code example, not measurements"),
        "12": (1, "EU AI Act ARTICLE number in the docs table, not a quantity"),
        "73,": (1, "the MCP tool count followed by a comma in prose; the claim itself is '73'. "
                "Was '68,' until 2026-08-25, when three places still said 68 or 71 while the server "
                "had 73 tool defs: README's documentation table, two CLAIMS descriptions, and BOTH "
                "og:description and twitter:description in claude-code.html -- the text search engines "
                "and link previews show. This gate reads numeric TOKENS, so a wrong number inside a "
                "correct sentence passes; see _prose_number_agrees below."),
        # The DOI. `10.5281` is the Zenodo registrant prefix and the rest is a record id -- an ADDRESS,
        # not a measurement. Declared with a count so that adding a second DOI has to be declared too,
        # rather than being absorbed silently by a bare name.
        "10.5281": (1, "Zenodo DOI registrant prefix in the citation link, an identifier not a quantity"),
    },
    "docs/DEEP_DIVE.md": {
        # The quickstart names the five examples that need Ed25519 by FILENAME, so their
        # numeric prefixes are published tokens. They are file names, not measurements.
        "04": (1, "the `04_encryption` example filename in the quickstart"),
        "06": (1, "the `06_gdpr_erasure_receipt` example filename in the quickstart"),
        "07": (1, "the `07_witness_pool` example filename in the quickstart"),
        "0": (8, "exit codes (0 = PASS), env-var settings (INSPEXIMUS_ECHO_GUARD=0, INSPEXIMUS_NOMIC_PREFIX=0, "
                 "snippet_chars>0), the bias limit h->0 and the weight ~0 in the threat model, and the "
                 "'0/18' after-column of the 1.90.0 chain-binding table"),
        "1": (15, "ordinals for the three numbered demos and the five numbered rules, exit codes in shell "
                  "examples, recall_any@1 and hit@1 as metric NAMES, counts in a pasted example output, "
                  "the '1/18' before-column of the 1.90.0 chain-binding table, and the '1' in the witness "
                  "quickstart's `inv7::total` example key"),
        "2": (17, "SOC 2 in a comparison cell and in the certification paragraph, the EU application dates "
                  "2 Dec 2027 / 2 Aug 2028 / 2 Aug 2026, ordinals for demo 2 and rule 2, the corroboration "
                  "threshold >=2, code literals (threshold=2, writes=2), the 'Cell 2' benchmark label, an "
                  "example output line, and the '2 minutes' quickstart heading"),
        "3": (3, "ordinals: demo 3, rule 3, and a count inside a pasted erasure-audit output"),
        "4": (5, "ACM TOS issue number 5(4), rule 4, a back-reference to rule 4, the '4-5 points' "
                 "the reinforce=False fix moves the LOCOMO pair by, and the '4/60' after-column of the "
                 "1.90.0 chain-binding table"),
        "5": (3, "ACM TOS volume 5(4), rule 5, and a back-reference to rule 5"),
        "6": (1, "AI Act Art. 26(6) -- an article sub-paragraph number"),
        "7": (1, "the invoice id in the witness quickstart's example memory "
                 "(\"invoice 7 total is 100 EUR\", key inv7::total) -- a made-up literal in a copy-paste "
                 "command, not a measurement"),
        "100": (2, "the invoice total in that same witness example, written twice on one line: once in "
                   "the remembered sentence and once as its --object value"),
        "8": (3, "the withdrawn '8/8' quoted inside the sentence that withdraws it (see "
                 "readme-supersession-8of8-withdrawn for the 0/24 the probe actually reports), and the "
                 "'8/60' before-column of the 1.90.0 chain-binding table"),
        "12": (3, "AI Act Art. 12 / Article 12 -- article numbers"),
        "15": (2, "AI Act Art. 15 -- an article number; and the fixture size '15 chains' in the sentence "
                  "introducing the 1.90.0 chain-binding table"),
        "18": (1, "the fixture size '18 unrelated pairs' in the sentence introducing the 1.90.0 "
                  "chain-binding table -- the negative control's denominator"),
        "17": (1, "GDPR Art. 17 -- an article number"),
        "19": (2, "AI Act Art. 19 -- an article number; and the Agora Lab id `19d802`"),
        "24": (1, "the OJ publication date 24 Jul 2026"),
        "26": (1, "AI Act Art. 26(6) -- an article number"),
        "27": (1, "the in-force date 27 Jul 2026"),
        "50": (1, "the documented default of INSPEXIMUS_MAX_K -- a configuration value, not a measurement"),
        "60": (2, "the '60 seconds' section heading -- a reading-time figure, not a measurement; and the "
                  "fixture size '60 prose sentences' introducing the 1.90.0 chain-binding table"),
        "90": (2, "the retention window in a copy-paste CLI example, written twice on one line as "
                  "\"90 days\" and --object 90d"),
        "03": (1, "a truncated record id (03dad5493e) inside a pasted erasure-audit output"),
        "3501": (1, "the Agora Lab experiment id `3501f1`"),
        "337961": (2, "a value FINGERPRINT (fp=337961f64779) in a pasted `inspeximus residue` output -- "
                      "twice, because the example finds the value in two stores"),
        "500": (1, "an illustrative sentence fed to regex_extractor ('The API rate limit is 500 rps')"),
        "800": (1, "NIST SP 800-88 -- a standard's number"),
        "1000": (2, "the example fact in the quickstart ('1000 req/min'), stated then recalled"),
        "1998": (1, "USENIX Security 1998 -- a citation year"),
        "1971": (1, "Lorden 1971 -- a citation year"),
        "1977": (2, "Biba 1977 -- a citation year, twice"),
        "1979": (1, "Doyle 1979 -- a citation year"),
        "1982": (2, "Lamport-Shostak-Pease 1982 -- a citation year, twice"),
        "1986": (1, "Moustakides 1986 -- a citation year"),
        "1987": (1, "Garcia-Molina & Salem 1987 -- a citation year"),
        "2001": (1, "Friedman-Resnick 2001 -- a citation year"),
        "2002": (1, "Douceur 2002 -- a citation year"),
        "2004": (1, "Prelec 2004 -- a citation year"),
        "2005": (2, "Cheng-Friedman 2005 -- a citation year, twice"),
        "2007,": (1, "Mobasher-Burke 2007 -- a citation year"),
        "2009": (2, "USENIX FAST 2009 and the ACM TOS 2009 journal version -- citation years"),
        "2009,": (1, "Mehta-Nejdl 2009 -- a citation year"),
        "2010": (2, "IEEE S&P 2010 and Viswanath 2010 -- citation years"),
        "2012,": (1, "SybilRank/Cao 2012 -- a citation year"),
        "2017": (1, "Blanchard 2017 -- a citation year"),
        "2018": (1, "Yin 2018 -- a citation year"),
        "2020": (1, "USENIX Security 2020 -- a citation year"),
        "2024": (1, "Zou 2024 (PoisonedRAG) -- a citation year"),
        "2026": (7, "dates: the three AI Act deferral dates, the source-scan date 24 Jul 2026, the probe "
                    "re-run date 2026-08-01, and the 2026-08-01 / 2026-07-25 pair on the discharged "
                    "LOCOMO caveat. The extractor measurement date 2026-07-20 went with the MemOps "
                    "paragraph 1.90.0 withdrew"),
        "2027": (2, "the AI Act Annex III application date, 2 Dec 2027"),
        "2028": (1, "the AI Act Annex I application date, 2 Aug 2028"),
        "3.0": (2, "a fictional library version in the code-guard example ('removed in 3.0')"),
        "1.0": (1, "a coverage ratio inside a pasted erasure-audit output"),
        "5000": (1, "the corrected example fact in the quickstart ('5000 req/min')"),
        "6962": (2, "RFC 6962 -- a standard's number, cited twice: in the integrity comparison row and again in the witness section's prior-art credit"),
        "9700": (1, "a port in a copy-paste command (--port 9700)"),
        "14227": (1, "Claude Code issue #14227 -- an issue number"),
        "27001": (1, "ISO 27001 -- a standard's number"),
        "94107": (1, "an illustrative ZIP code fed to regex_extractor"),
        "2606.26511": (1, "arXiv 2606.26511 (MemStrata / Yadav) -- an identifier"),
    },
    "MCP_LISTINGS.md": {
        "1": (2, "the ordinal for submission route 1, and 'route 1 below' referring to it"),
        "2": (1, "the ordinal for submission route 2"),
        "3": (1, "the ordinal for submission route 3"),
        "4": (1, "the ordinal for submission route 4"),
        "5": (1, "the ordinal for submission route 5"),
        "12": (1, "the historical '12 tools' figure, quoted inside the record of an earlier correction"),
        "26": (1, "how far short the stale count was -- arithmetic on two registered figures (56 - 30)"),
        "30,": (1, "the stale '30' quoted inside the record of the earlier correction"),
        "2026": (2, "the correction date 2026-07-21 and 'June 2026'"),
        "404": (1, "\"404s on PyPI\" -- an HTTP status used as a verb"),
        "4413": (1, "PR #4413 -- a pull-request number"),
    },
    "index.html": {
        # A SETTING, not a measurement: the temperature the shared judge is pinned at.
        "0.0": (1, "the judge's temperature in the benchmark caveat -- the temperature the shared judge is pinned at; a parameter, not a result"),
        "2026": (1, "the re-measurement date in the benchmark caveat, not a quantity"),
        "0": (1, "the schema.org offer price, '0' USD -- a JSON-LD literal"),
        "01": (1, "a section beat label"),
        "02": (1, "a section beat label"),
        "03": (1, "a section beat label"),
        "17": (1, "GDPR Art. 17 -- an article number"),
        "200": (2, "'200 OK' -- an HTTP status code"),
        "800": (1, "NIST SP 800-88 -- a standard's number"),
        "6962": (1, "RFC 6962 -- a standard's number"),
    },
}

# A digit run a reader sees. The first version of this pattern missed two shapes, and both were
# FALSE-SAFE misses -- the scanner reported a page clean while it carried an unregistered figure:
#   * a UNIT-GLUED number (`--object 90d`, `42ms`): the trailing lookahead demanded a non-word char,
#     so the 90 already on README.md:246 was invisible, and the registry's declared count of 1 for
#     token "90" only looked right because the checker shared the scanner's blind spot. A guard and
#     its target computed by the same broken rule agree with each other and with nothing else.
#   * a NEGATIVE number (`z=-4.79`, `(-42%)`): the lookbehind ate the sign and the token with it.
# The lookbehind still refuses a leading `-` for the UNSIGNED pattern, because allowing it would turn
# every date (`2026-08-01`) and compound (`AES-256-GCM`, `SP 800-88`) into a shower of new tokens. The
# sign is picked up by a second, narrower pattern that only fires where a minus can actually BE a sign.
_NUM = re.compile(r"(?<![\w/.\-:])(\d[\d,]*(?:\.\d+)?)(?!\d)")
_NEG = re.compile(r"(?<=[\s(\[=~,;:>])(-\d[\d,]*(?:\.\d+)?)(?!\d)")

#: Top-level directories a "run this to reproduce" path can point into. Kept in ONE place: the first
#: version had this prefix list copy-pasted into three checks with three different sets of directories,
#: none of which knew about `docs/`, `bench/` or `site/` -- three copies of one rule, all too narrow,
#: agreeing with each other. A consistency control cannot see a defect its copies share.
ARTIFACT_DIRS = ("probes", "tests", "tools", "examples", "inspeximus", "perf", "bench", "benchmarks",
                 "docs", "site", "packages", "audits", "assets", "assets_readme")
ARTIFACT_PATH = re.compile(
    r"(?<![\w/.\-])((?:" + "|".join(ARTIFACT_DIRS) + r")/[\w/.\-]+\.\w+)")

# Spans that are not read as numbers by a reader: link targets, URLs, HTML tags, semver strings.
# Kept deliberately SHORT. Every span here is a hole, so each one has to be self-evidently not a claim.
_MASKS = (
    re.compile(r"https?://\S+"),                 # a URL
    re.compile(r"\]\([^)\s]*\)"),                # a markdown link target
    re.compile(r"^\s*\[[^\]]+\]:\s*\S+", re.M),  # a markdown reference definition
    re.compile(r"\bv?\d+\.\d+\.\d+[\w.\-]*"),    # a semver / release string
    re.compile(r"<!--.*?-->", re.S),             # a comment
)


def _blank(m):
    """Blank a span while preserving length AND line breaks, so offsets and line numbers survive."""
    return "".join("\n" if c == "\n" else " " for c in m.group(0))


def _readable(text: str, is_html: bool) -> str:
    """The part of a file a reader actually sees, with everything else blanked out in place.

    For HTML this is the crux: `data-count="58"` RENDERS as the number 56 (a script animates it into the
    element), so it is a published figure even though it lives in an attribute. It is hoisted out of the
    tag before tags are stripped -- the first version of this function stripped the tag first, which
    silently exempted the exact figure that was wrong on the homepage. A masker that cannot see the
    claim reports the page clean.
    """
    if is_html:
        text = re.sub(r"<style\b.*?</style>", _blank, text, flags=re.S | re.I)
        text = re.sub(r"<script\b(?![^>]*ld\+json).*?</script>", _blank, text, flags=re.S | re.I)
        text = re.sub(
            r'<([a-zA-Z][\w-]*)([^>]*?)\sdata-count="([\d.]+)"([^>]*)>',
            lambda m: " " + m.group(3) + " <" + m.group(1) + m.group(2) + m.group(4) + ">",
            text,
        )
        text = re.sub(r"<[^>]+>", _blank, text)
    else:
        # Bounded to a single line: an unmatched `<` in prose would otherwise let the tag stripper run
        # away across paragraphs and blank out numbers it was never meant to touch.
        text = re.sub(r"<[^>\s\n][^>\n]*>", _blank, text)
    for mk in _MASKS:
        text = mk.sub(_blank, text)
    return text


def scan_numbers(path):
    """Every numeric token a reader sees in `path`, as (line_no, token, source_line)."""
    p = pathlib.Path(path)
    raw = p.read_text(encoding="utf-8", errors="replace")
    readable = _readable(raw, p.suffix.lower() in (".html", ".htm"))
    raw_lines = raw.splitlines()
    out = []
    for i, line in enumerate(readable.splitlines()):
        src = raw_lines[i] if i < len(raw_lines) else line
        hits = [(m.start(), m.group(1)) for m in _NUM.finditer(line)]
        hits += [(m.start(), m.group(1)) for m in _NEG.finditer(line)]
        for _pos, token in sorted(hits):
            out.append((i + 1, token, src.strip()))
    return out


def _repo_root():
    return pathlib.Path(__file__).resolve().parent


def audit_numbers(root=None, verify_commands=False):
    """Classify every published number. Returns (problems, stats).

    Six independent ways to fail, because a single one would only catch a single shape of drift:
      UNREGISTERED    a number is printed that no registry row accounts for
      COUNT-DRIFT     a non-claim token's occurrence count moved
      STALE-PIN       a claim's pinned sentence is gone -- the row now describes nothing
      STALE-NONCLAIM  a declared non-claim token no longer appears at all
      BROKEN-COMMAND  a claim's reproduction command names a path that does not exist
      LIVE-MISMATCH   a self-referential figure disagrees with the code it describes
    """
    root = pathlib.Path(root or _repo_root())
    problems, stats = [], {"published": 0, "claims": 0, "by_status": {}}

    claims_by_file = {}
    for c in NUMBER_CLAIMS:
        claims_by_file.setdefault(c["file"], []).append(c)

    for fname in SURFACE:
        path = root / fname
        if not path.exists():
            problems.append(("MISSING-SURFACE", fname, f"{fname} is registered as a surface but does not exist"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rows = claims_by_file.get(fname, [])
        for c in rows:
            if c["pin"] not in text:
                problems.append(("STALE-PIN", fname, f"claim {c['id']!r} pins {c['pin']!r}, which is no longer in {fname}"))
        declared = dict(NON_CLAIM_TOKENS.get(fname, {}))
        seen = collections.Counter()
        for line_no, token, src in scan_numbers(path):
            stats["published"] += 1
            owner = next((c for c in rows if token in c["tokens"] and c["pin"] in src), None)
            if owner is not None:
                stats["claims"] += 1
                continue
            seen[token] += 1
        for token, count in seen.items():
            if token not in declared:
                problems.append(("UNREGISTERED", fname,
                                 f"{token!r} x{count} is published in {fname} but has no entry in "
                                 f"NUMBER_CLAIMS or NON_CLAIM_TOKENS"))
            elif declared[token][0] != count:
                problems.append(("COUNT-DRIFT", fname,
                                 f"{token!r} appears {count}x, registry declares {declared[token][0]}x"))
        for token, (count, _why) in declared.items():
            if token not in seen:
                problems.append(("STALE-NONCLAIM", fname,
                                 f"{token!r} is declared {count}x as a non-claim but no longer appears"))

    for c in NUMBER_CLAIMS:
        stats["by_status"][c["status"]] = stats["by_status"].get(c["status"], 0) + 1
        for tok in ARTIFACT_PATH.findall(c["command"]):
            if not (root / tok).exists():
                problems.append(("BROKEN-COMMAND", c["file"],
                                 f"claim {c['id']!r} names {tok!r}, which does not exist"))

    problems.extend(_prose_number_agrees())

    if verify_commands:
        problems.extend(_unearned_statuses(root, stats))

    problems.extend(_live_consistency(root))
    return problems, stats



def _unearned_statuses(root, stats):
    """A status that says REPRODUCIBLE must be EARNED BY A RUN, not asserted by whoever wrote the row.

    This exists because the hole was found from outside. @mioimotoai-lgtm cloned the repo, ran the
    command five rows of this table give as their reproduction, and it died with FileNotFoundError
    before argparse -- while the table certified those rows REPRODUCIBLE-WITH-DEPS, a status this
    file defines as "committed command, but needs a service/dataset we cannot ship". That promise is
    about DEPENDENCIES. No dependency would have fixed a hardcoded relative path.

    Nothing checked it. BROKEN-COMMAND verified the command names a file that EXISTS; whether the
    command could start was a human's opinion typed into a constant, inside the instrument this
    project publishes as machine-checked. A claim that cannot fail is the defect we write about.

    So: execute each cited script's MODULE LEVEL from a directory that is not the repo root, with no
    OPENAI_API_KEY. `runpy` with a run_name other than `__main__` runs the imports and the body but
    not main(), so nothing is benchmarked and nothing is billed, while the class that produced #1 is
    exercised.

    A MISSING THIRD-PARTY PACKAGE IS NOT A FAILURE HERE -- it is precisely what WITH-DEPS promises,
    and a reader fixes it with pip. Failing to import THIS package is, because that means the
    script's own path handling is wrong. A missing file is, because #1 was a missing file.
    """
    import re as _re
    import subprocess as _sub

    # ONLY THE SCRIPT THE COMMAND ACTUALLY EXECUTES. The first version of this check pulled every
    # .py path out of the command string and ran it, which is the same defect it was written to
    # catch: it did not measure what the claim promises. `readme-mcp-tools` reproduces by READING
    # inspeximus/mcp_server.py as text and counting @mcp.tool in it -- the file is a data argument,
    # never executed -- and the check reported the claim as unearned because that file cannot be run
    # as a script from a foreign directory. It is not supposed to be. CI caught it on the first run.
    #
    # So: a command of the form `python <path.py> ...` names a script to probe. A `python -c "..."`
    # command is self-contained and executes nothing else, whatever paths appear inside its code.
    scripts = {}
    for c in NUMBER_CLAIMS:
        if not c["status"].startswith("REPRODUCIBLE"):
            continue
        parts = c["command"].split()
        if len(parts) < 2 or not parts[0].startswith("python"):
            continue
        if parts[1] == "-c" or parts[1] == "-m":
            continue                      # runs inline code or a module, not a script path
        tok = parts[1]
        if tok.endswith(".py") and (root / tok).exists():
            scripts.setdefault(tok, []).append(c["id"])

    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    tmp = tempfile.mkdtemp(prefix="claims_earned_")
    out = []
    for script, claim_ids in sorted(scripts.items()):
        code = "import runpy;runpy.run_path(%r, run_name='__audit_probe__')" % str(root / script)
        try:
            p = _sub.run([sys.executable, "-c", code], cwd=tmp, env=env,
                         capture_output=True, text=True, timeout=300)
        except _sub.TimeoutExpired:
            out.append(("UNEARNED-STATUS", script,
                        f"{script} did not finish its module level in 300s from a foreign cwd; "
                        f"claims {claim_ids} rest on it. If the machine is busy this is the harness, "
                        f"not the script -- re-run it alone before believing it"))
            continue
        stats["commands_verified"] = stats.get("commands_verified", 0) + 1
        if p.returncode == 0:
            continue
        err = p.stderr or ""
        m = _re.search(r"ModuleNotFoundError: No module named '([A-Za-z0-9_.]+)'", err)
        if m and m.group(1).split(".")[0] not in {"inspeximus", "probes"}:
            stats.setdefault("deps_declined", []).append(f"{script} needs {m.group(1)}")
            continue
        tail = err.strip().splitlines()[-1][:160] if err.strip() else "no stderr"
        out.append(("UNEARNED-STATUS", script,
                    f"{script} cannot start from a directory that is not the repo root, and not for "
                    f"a missing third-party package. Claims {claim_ids} are certified "
                    f"REPRODUCIBLE by assertion, not by a run -- {tail}"))
    shutil.rmtree(tmp, ignore_errors=True)
    return out


_COLLECTED = {}   # process-level cache: collection costs ~18s, and several tests audit the same root


def _collected_test_count(root):
    """How many tests this repo ACTUALLY collects, by collecting them.

    Registered-and-reproducible is not the same as still-true. The registry checks that a number has an
    entry, that its pin resolves, and that its command names a real file -- it never runs the command, so
    a count that was right when it was written stays green forever after it stops being right. Measured:
    the README published 2,793 while the suite collected 2,797, and every gate was green, because nothing
    in the chain re-derived the figure. Counting `def test_` statically would not do it either: parametrize
    multiplies cases, so the static number is a different quantity that happens to look like this one.

    Returns None when it cannot count, and None must NOT be read as agreement -- the caller says so.
    """
    if root in _COLLECTED:
        return _COLLECTED[root]
    # ONLY this repository. `audit_numbers` is also run against temp COPIES of the whole tree -- the
    # mutation-score test makes one per mutant -- and collecting a copied suite costs ~20 minutes each,
    # for an answer about a tree nobody publishes. Measured: it turned a 20-minute suite into one that
    # was 10% done after 25. The published numbers belong to this checkout, so this counts this checkout.
    if pathlib.Path(root).resolve() != _repo_root().resolve():
        return None
    tests = root / "tests"
    if not tests.is_dir():
        _COLLECTED[root] = None
        return None
    if importlib.util.find_spec("pytest") is None:
        # The counting TOOL is absent, which is a different fact from "the count failed", and the caller
        # treats it differently. The audit job installs no test extras, so demanding a collection there
        # made a green repository red for the absence of pytest. The guarantee still binds where it can
        # be evaluated: the test job has pytest and enforces it there.
        _COLLECTED[root] = None
        return None
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", str(tests), "-q", "--collect-only",
                            "-p", "no:randomly", "--continue-on-collection-errors"],
                           cwd=str(root), capture_output=True, text=True, timeout=600,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.SubprocessError):
        _COLLECTED[root] = None
        return None
    m = re.search(r"(\d+) tests? collected", r.stdout or "")
    _COLLECTED[root] = int(m.group(1)) if m else None
    return _COLLECTED[root]


def _live_consistency(root):
    """Figures that describe THIS repo must equal what this repo actually contains.

    A published count is the easiest number to leave behind, because nothing breaks when it goes wrong:
    the MCP tool count was simultaneously 30 (MCP_LISTINGS.md), 15 and 56 (the homepage) while the server
    registered 56. Three surfaces, one truth, no error anywhere. So the count is read from the code.
    """
    out = []
    server = root / "inspeximus" / "mcp_server.py"
    if not server.exists():
        return [("LIVE-MISMATCH", "inspeximus/mcp_server.py", "the MCP server is gone; the tool count cannot be checked")]
    live = len(re.findall(r"@mcp\.tool\(\)", server.read_text(encoding="utf-8", errors="replace")))
    for fname, pat in (("MCP_LISTINGS.md", r"inspeximus-mcp`, (\d+) tools"),
                       ("MCP_LISTINGS.md", r"\*\*Tools \((\d+)\):\*\*"),
                       ("index.html", r'data-count="(\d+)">0</b><span>MCP tools'),
                       ("index.html", r">(\d+) tools any MCP host can call<"),
                       ("docs/DEEP_DIVE.md", r"`inspeximus-mcp`, (\d+) tools")):
        p = root / fname
        if not p.exists():
            continue
        m = re.search(pat, p.read_text(encoding="utf-8", errors="replace"))
        if m is None:
            out.append(("LIVE-MISMATCH", fname, f"expected a tool count matching {pat!r}; found none"))
        elif int(m.group(1)) != live:
            out.append(("LIVE-MISMATCH", fname, f"publishes {m.group(1)} MCP tools; the server registers {live}"))

    # The suite size, counted by collecting it. BOTH surfaces that carry it are checked, including the
    # badge -- the badge number lives inside a URL, and scan_numbers deliberately does not read URLs, so
    # `tests-2793` was a published figure that no gate had ever looked at. The most-read number on the
    # page was the one number outside the system.
    collected = _collected_test_count(root)
    # In scope when this IS the repository whose numbers are published, or when a caller seeded the count
    # explicitly (the controls do, so they exercise the comparison without paying for a collection). A
    # temp COPY of the tree is neither: nobody publishes its README, and counting it would say nothing
    # about ours. Scoping is not skipping -- for the real repo, "could not count" stays loud below.
    in_scope = pathlib.Path(root).resolve() == _repo_root().resolve() or root in _COLLECTED
    if in_scope and (root / "tests").is_dir():
        if collected is None and importlib.util.find_spec("pytest") is None:
            pass          # no pytest here: out of scope, not a failure. See _collected_test_count.
        elif collected is None:
            out.append(("LIVE-MISMATCH", "tests/",
                        "pytest is here but the suite could not be collected, so the published test "
                        "count was NOT checked -- which is not the same as checked"))
        else:
            # A FLOOR, not an equality. An exact published count is unstable under its own maintenance:
            # adding the eight controls below this check moved it 2797 -> 2804, and removing one moved it
            # again, so the number was wrong twice in ten minutes purely because it was being guarded.
            #
            # And the floor must hold in the LEANEST environment, not the richest. Measured on CI: this
            # checkout collects 2,804 locally with every optional extra installed and 2,637 on a runner
            # without them, because modules that importorskip at module level are never collected at all.
            # A floor of 2,800 therefore called a perfectly healthy CI a shrinking suite. The published
            # number is a property of the repository; the count is a property of the repository AND the
            # environment. So publish a floor that is true everywhere and let a full install exceed it --
            # understating is safe, overstating is a lie. The floor still FAILS the
            # moment the suite shrinks past it, which is the direction that would be a lie.
            for fname, pat, what in (("README.md", r"badge/tests-([\d,]+)%2B-", "the tests badge"),
                                     ("README.md", r"\*\*([\d,]+)\+ tests\*\*", "the prose test count")):
                p = root / fname
                if not p.exists():
                    continue
                m = re.search(pat, p.read_text(encoding="utf-8", errors="replace"))
                if m is None:
                    out.append(("LIVE-MISMATCH", fname, f"{what} is gone; it can no longer be checked"))
                elif collected < int(m.group(1).replace(",", "")):
                    out.append(("LIVE-MISMATCH", fname,
                                f"{what} publishes a floor of {m.group(1)}; the suite collects only "
                                f"{collected}"))

    # The integration counts come from the ledger the conformance runner writes, for the same reason the
    # tool count comes from the server: the homepage said "Drop-in for" nine frameworks with no qualifier
    # while a JSON file in this repo recorded three of them broken. Nothing reconciled the two.
    ledger = root / "docs" / "integration_conformance.json"
    # EVERY surface that states the counts, not just the homepage. The README now carries them
    # too, and a second copy of a number is a second place for it to go stale -- checking one of
    # them is how the MCP tool count came to be 30, 15 and 56 simultaneously.
    for _surface in ("index.html", "README.md"):
      site = root / _surface
      if ledger.exists() and site.exists():
          try:
              rows = json.loads(ledger.read_text(encoding="utf-8"))["integrations"]
          except (ValueError, KeyError) as e:
              out.append(("LIVE-MISMATCH", "docs/integration_conformance.json",
                          f"the conformance ledger could not be read ({e}), so the published adapter "
                          f"counts were NOT checked -- which is not the same as checked"))
          else:
              broken = sum(1 for v in rows.values() if v.get("broken_against"))
              m = re.search(r"(\d+) of (\d+) verified against current upstream, (\d+) recorded broken",
                            site.read_text(encoding="utf-8", errors="replace"))
              if m is None:
                  # REQUIRED on both, not optional on one. These counts are the honest form of a
                  # "works with" logo wall -- they name what is broken -- and a claim that can be
                  # deleted without anything noticing is a claim we would eventually stop making
                  # by accident.
                  out.append(("LIVE-MISMATCH", _surface,
                              "the adapter conformance counts are gone; they cannot be checked"))
              elif (int(m.group(1)), int(m.group(2)), int(m.group(3))) != (len(rows) - broken, len(rows), broken):
                  out.append(("LIVE-MISMATCH", _surface,
                              f"publishes {m.group(1)}/{m.group(2)} verified and {m.group(3)} broken; the "
                              f"ledger records {len(rows) - broken}/{len(rows)} and {broken}"))

    readme = root / "docs/DEEP_DIVE.md"
    if readme.exists():
        m = re.search(r"(\d+) passed · (\d+) FAILED · (\d+) skipped · (\d+) not testable here",
                      readme.read_text(encoding="utf-8", errors="replace"))
        if m is None:
            out.append(("LIVE-MISMATCH", "docs/DEEP_DIVE.md", "the example audit summary is gone; it cannot be checked"))
        else:
            if int(m.group(1)) != len(CHECKS):
                out.append(("LIVE-MISMATCH", "docs/DEEP_DIVE.md",
                            f"the example run shows {m.group(1)} passing checks; this file defines {len(CHECKS)}"))
            if int(m.group(4)) != len(NOT_TESTABLE_HERE):
                out.append(("LIVE-MISMATCH", "docs/DEEP_DIVE.md",
                            f"the example run shows {m.group(4)} not-testable claims; this file lists "
                            f"{len(NOT_TESTABLE_HERE)}"))
            # The middle two counts were captured and never read -- so the page could advertise an
            # example run with failures in it and this check would have shrugged. Four numbers on that
            # line, two of them checked, is not "the line is checked".
            if (int(m.group(2)), int(m.group(3))) != (0, 0):
                out.append(("LIVE-MISMATCH", "docs/DEEP_DIVE.md",
                            f"the example run advertises {m.group(2)} FAILED / {m.group(3)} skipped; the "
                            f"example is supposed to show a clean run"))
    return out


def render_claims_doc(root=None):
    """Render docs/CLAIMS.md from the registry, so the document cannot drift from the checker."""
    root = pathlib.Path(root or _repo_root())
    _, stats = audit_numbers(root)
    n = len(NUMBER_CLAIMS)
    repro = sum(1 for c in NUMBER_CLAIMS if c["status"].startswith("REPRODUCIBLE"))
    L = []
    A = L.append
    A("# Published numbers — every figure, its command, and its status")
    A("")
    A("<!-- GENERATED by `python claims_audit.py --write-claims`. Do not edit by hand:")
    A("     the registry in claims_audit.py is the source, and a test fails if this file drifts from it. -->")
    A("")
    A("A number a reader cannot reproduce is not evidence. This table is the whole reader-facing surface's")
    A("quantitative content, each figure against the exact command that produces it.")
    A("")
    A("**Scope, stated so a green result cannot be over-read.** The audit *enforces* registration on")
    A("`README.md`, `MCP_LISTINGS.md` and `index.html`: every numeric token a reader sees on those three")
    A("must be a row below or a declared non-claim, and `python claims_audit.py --numbers` fails otherwise.")
    A("`CHANGELOG.md` and `docs/` are **not** token-enforced — they are covered only by the weaker check")
    A("that every artifact path they name exists. Their numbers are not audited here, and reading this")
    A("page as \"every number in the project is backed\" would be exactly the over-read it exists to prevent.")
    A("")
    A("## The ratio")
    A("")
    A(f"- **{stats['published']}** numeric tokens are published across the "
      f"{len(SURFACE)} enforced files: {', '.join(SURFACE)}.")
    A(f"- **{stats['claims']}** of those are quantitative claims, in **{n}** registry rows below.")
    A(f"- **{repro}** rows ({repro}/{n}) are reproducible by a command committed to this repository")
    A("  (`REPRODUCIBLE` needs nothing but this checkout; `REPRODUCIBLE-WITH-DEPS` needs a service or")
    A("  dataset we cannot redistribute, named in the command column).")
    A(f"- The remaining {n - repro} are `PENDING-HARNESS`, `EXTERNAL` or `WITHDRAWN`.")
    A(f"- The other {stats['published'] - stats['claims']} tokens are declared non-claims — citation years,")
    A("  article numbers, ordinals, ports, example literals — each with a reason and an exact expected")
    A("  count, so adding one silently is not possible either.")
    A("")
    A("Counts by status:")
    A("")
    for s in STATUSES:
        A(f"- `{s}` — {stats['by_status'].get(s, 0)}")
    A("")
    A("## The table")
    A("")
    A("| # | file | figure(s) | claim | status | command that reproduces it |")
    A("|---|---|---|---|---|---|")
    for i, c in enumerate(sorted(NUMBER_CLAIMS, key=lambda x: (x["file"], x["id"])), 1):
        toks = " ".join(f"`{t}`" for t in c["tokens"]) or "—"
        cmd = f"`{c['command']}`" if c["command"] else "—"
        A(f"| {i} | `{c['file']}` | {toks} | {c['claim']} | **{c['status']}** | {cmd} |")
    A("")
    A("## Notes")
    A("")
    for c in sorted(NUMBER_CLAIMS, key=lambda x: (x["file"], x["id"])):
        if c.get("note"):
            A(f"- **{c['id']}** — {c['note']}")
    A("")
    A("## Known unenforced numbers")
    A("")
    A(f"These are outside the {len(SURFACE)} token-enforced files, so the guard above does "
      "**not** cover them.")
    A("They are listed because \"absent from the table\" and \"not a problem\" are different statements,")
    A("and here only the first one is true. None may be promoted onto the reader-facing surface while it")
    A("still says `PENDING-HARNESS`.")
    A("")
    A("| where | figure | status | why it is here |")
    A("|---|---|---|---|")
    for where, figure, status, why in UNENFORCED_NOTES:
        A(f"| {where} | `{figure}` | **{status}** | {why} |")
    A("")
    A("## What was withdrawn, and why that is the point")
    A("")
    A("Removing a number we cannot back is a win. Every `WITHDRAWN` row above is a figure this audit")
    A("deleted from the reader-facing surface rather than dress up: either no artifact in this repository")
    A("produces it, or the artifact it named does not exist. Showing the gap is what makes the rest")
    A("worth reading.")
    A("")
    return "\n".join(L)


def counts_as_failure(ok, auditing_history: bool) -> bool:
    """Does this outcome fail the gate?

    Pulled out of main() so it can be tested at all: the rule that a SKIP counts against the exit code
    lived inline, and a mutation removing it survived the suite because no check happens to return None
    today. A guard nothing exercises is a guard that will be silently reverted.

    False -> always a failure. None means "this build does not have it", which is a fact about the
    ARTIFACT only when auditing a historical release; against the working tree or the latest wheel it
    means a claim in our README has no implementation behind it.
    """
    if ok is False:
        return True
    return ok is None and not auditing_history


def _run(idx):
    name, fn = CHECKS[idx]
    t0 = time.time()
    try:
        ok, ev = fn()
    except Exception as e:
        ok, ev = False, f"raised {type(e).__name__}: {e}"
    return idx, name, ok, ev, time.time() - t0


def fetch_wheel(version, workdir):
    cmd = [sys.executable, "-m", "pip", "download",
           f"inspeximus=={version}" if version else "inspeximus",
           "--no-deps", "-d", str(workdir)]
    subprocess.run(cmd, capture_output=True, check=True)
    wheel = sorted(workdir.glob("*.whl"))[0]
    import zipfile
    pkg = workdir / "pkg"
    zipfile.ZipFile(wheel).extractall(pkg)
    return wheel, pkg


def report_numbers(root=None, verify_commands=False):
    """Print the published-number audit. Returns the number of problems (0 = clean)."""
    problems, stats = audit_numbers(root, verify_commands=verify_commands)
    n = len(NUMBER_CLAIMS)
    repro = sum(1 for c in NUMBER_CLAIMS if c["status"].startswith("REPRODUCIBLE"))
    print("=" * 92)
    print("PUBLISHED-NUMBER AUDIT — " + ", ".join(SURFACE))
    print("=" * 92)
    print(f"  {stats['published']} numeric tokens published; {stats['claims']} of them are quantitative "
          f"claims in {n} registry rows")
    print(f"  {repro}/{n} rows reproducible by a committed command "
          f"(REPRODUCIBLE {stats['by_status'].get('REPRODUCIBLE', 0)} + "
          f"REPRODUCIBLE-WITH-DEPS {stats['by_status'].get('REPRODUCIBLE-WITH-DEPS', 0)})")
    for s in STATUSES:
        print(f"    {s:24s} {stats['by_status'].get(s, 0)}")
    print(f"  {stats['published'] - stats['claims']} tokens are declared non-claims (citation years, "
          f"article numbers, ordinals, ports, example literals)")
    if problems:
        print("\n  PROBLEMS:")
        for kind, where, msg in problems:
            print(f"    [{kind}] {where}: {msg}")
    else:
        print("\n  every published number is registered, every pin resolves, every command names a real file")
    print("=" * 92)
    return len(problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="audit a specific released version")
    ap.add_argument("--local", action="store_true", help="audit the working tree instead of PyPI")
    ap.add_argument("--workers", type=int, default=min(12, (os.cpu_count() or 4) - 2))
    ap.add_argument("--verify-commands", action="store_true",
                    help="EARN the REPRODUCIBLE statuses: run each cited script's module level from "
                         "a foreign directory with no key, and fail any claim whose command cannot "
                         "start. Costs ~90s; see _unearned_statuses for why it exists.")
    ap.add_argument("--numbers", action="store_true",
                    help="audit only the PUBLISHED NUMBERS (offline; no wheel download)")
    ap.add_argument("--write-claims", action="store_true",
                    help="regenerate docs/CLAIMS.md from the registry")
    ap.add_argument("--root", default=None,
                    help="audit the surface under this directory instead of the repo this file sits in "
                         "(so a test can point the REAL entrypoint at a mutated copy)")
    a = ap.parse_args()

    if a.write_claims:
        out = pathlib.Path(a.root or _repo_root()) / "docs" / "CLAIMS.md"
        out.write_text(render_claims_doc(a.root), encoding="utf-8", newline="\n")
        print(f"wrote {out}")
        return 1 if report_numbers(a.root, verify_commands=a.verify_commands) else 0

    if a.numbers:
        return 1 if report_numbers(a.root, verify_commands=a.verify_commands) else 0

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="inspeximus_claims_"))
    if a.local:
        pkg, src = pathlib.Path(__file__).resolve().parent, "working tree"
        sha = "n/a"
    else:
        wheel, pkg = fetch_wheel(a.version, tmp)
        src, sha = wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest()
    os.environ[PKG_ENV] = str(pkg)

    sys.path.insert(0, str(pkg))
    import inspeximus as inspeximus
    print("=" * 92)
    print(f"auditing : {src}")
    print(f"version  : {getattr(inspeximus, '__version__', '?')}")
    if sha != "n/a":
        print(f"sha256   : {sha}")
    print(f"checks   : {len(CHECKS)} on {a.workers} workers")
    print("=" * 92)

    results = [None] * len(CHECKS)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for idx, name, ok, ev, dt in ex.map(_run, range(len(CHECKS))):
            results[idx] = (name, ok, ev, dt)

    # A SKIP counts as a FAILURE unless we are auditing a HISTORICAL release, which is the only case where
    # "this build does not have it" is a fact about the artifact rather than about our claim. Until now a
    # missing feature and a raised exception both produced [SKIP], "0 FAILED" and exit 0 -- so deleting a
    # capability the README asserts would have kept the pre-publish gate green.
    auditing_history = bool(a.version)
    npass = nfail = nskip = 0
    for name, ok, ev, dt in results:
        counts = counts_as_failure(ok, auditing_history)
        tag = "PASS" if ok is True else ("FAIL" if counts else "SKIP")
        npass += ok is True
        nfail += counts
        nskip += (ok is None) and not counts
        print(f"[{tag}] {name}")
        print(f"       {ev}")

    print("\nNOT TESTABLE FROM THIS PACKAGE (claims about other systems — never counted as passing):")
    for c in NOT_TESTABLE_HERE:
        print(f"  [ -- ] {c}")

    print("\n" + "=" * 92)
    print(f"{npass} passed · {nfail} FAILED · {nskip} skipped · {len(NOT_TESTABLE_HERE)} not testable here")
    print("=" * 92)
    shutil.rmtree(tmp, ignore_errors=True)

    # The number audit runs on every invocation and counts toward the exit code. Making it opt-in would
    # have meant the default run kept passing while the page filled up with unbacked figures again --
    # which is precisely how the 31 broken receipt paths and three different MCP tool counts survived.
    nnum = report_numbers()
    return 1 if (nfail or nnum) else 0


if __name__ == "__main__":
    raise SystemExit(main())
