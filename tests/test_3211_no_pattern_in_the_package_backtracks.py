"""No regular expression in inspeximus grows worse than linear on its own keywords (3.21.1, AUDIT-A F-1, F-4).

The class check, in CI. tests/_redos_attack.py collects every compiled pattern, every regex-looking string literal in
a module that imports `re`, and every object that stands in for a regex, and attacks each with input built from its
own keywords. The first 3.21.1 scan ran outside the repository and missed 13 patterns: strings in a list handed to
re.search at run time, and rules whose keyword its pumps never repeated. This test keeps that class closed: a new
pattern that backtracks fails here on the push that adds it.

EXCLUDED, BY NAME AND WITH THE REASON CHECKED: the seven key-extraction patterns and the clause split inside
derive_key. They backtrack on long runs of whitespace, which derive_key collapses to one space before any of them
runs (3.20). The test asserts that derive_key is their only caller, and attacks derive_key with each one's pumps."""
import ast
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _redos_attack as ra  # noqa: E402

from inspeximus import core  # noqa: E402

EXCLUDED_NAMES = ("_EX_REL", "_EX_OF", "_EX_HEAD", "_EX_MINE", "_EX_CHANGE", "_EX_VERB", "_EX_IS")
CLAUSE_SPLIT = r"[,;]|\s+--\s+|\s+—\s+"
OLD_FRAME = r"</([A-Za-z_][\w.:-]{1,32})>\s*(?:<([A-Za-z_][\w.:-]{1,32})>.*?</\2>\s*)+\Z"
OLD_CHECKOUT = r"\bgit\s+checkout\b[^\n]*?\s(?:--\s|-f\b|--force\b)"


def _excluded():
    return {getattr(core, n).pattern for n in EXCLUDED_NAMES} | {CLAUSE_SPLIT}


@pytest.fixture(scope="module")
def collected():
    return ra.everything()


def test_control_the_attack_flags_the_old_frame_guard_and_an_old_replay_rule():
    # The four kinds the 3.21.1 rounds fixed, as they were: exponential tags, a repeated keyword before a flag, a
    # pattern held as a string in _PRE_PATTERNS (two of them), and the memory index link reader.
    for pat, flags in ((OLD_FRAME, re.S), (OLD_CHECKOUT, re.I),
                       (r"gh\s+(issue|pr)\s+comment\b|gh\s+api[^|]*\bcomments\b", re.I),
                       (r"git\s+push\s+.*--force", re.I), (r"\]\(([^)]+\.md)\)", 0)):
        rx = re.compile(pat, flags)
        assert ra.attack(rx.search, ra.pumps(pat, flags)), "the harness cannot see a known quadratic pattern: %s" % pat


class _Clocked:
    """A callable that takes `seconds(len(text))` of busy time, and stalls once at one size, as a loaded runner does."""

    def __init__(self, seconds, stall_at=None, stall=0.0):
        self.seconds, self.stall_at, self.stall, self.calls = seconds, stall_at, stall, {}

    def __call__(self, text):
        n = len(text)
        self.calls[n] = self.calls.get(n, 0) + 1
        wait = self.seconds(n)
        if self.stall_at is not None and n >= self.stall_at and self.calls[n] <= 3 and not getattr(self, "_hit", 0):
            wait += self.stall
            if self.calls[n] == 3:
                self._hit = 1                                  # the stall hits the three calls of ONE size only
        end = time.perf_counter() + wait
        while time.perf_counter() < end:
            pass


def test_one_stall_on_a_linear_call_is_not_a_verdict():
    # CI run 39372338135 flagged the linear _secrets._ASSIGN on one stall: 0.001 s at 7,047 characters, 0.015 s at
    # 28,863. Here a linear call (4 ms at 10,000 characters) stalls 40 ms once on its way up the ladder. The exponent
    # over the ladder reads above 1.6, so the old rule flagged it; the confirmation at 4x the input must clear it.
    linear = _Clocked(lambda n: 4e-7 * n, stall_at=15000, stall=0.04)
    assert ra.attack(linear, [("", "ab")]) is None
    # CONTROL: the stall really reaches the ladder, and without the confirmation it is a verdict.
    seen = _Clocked(lambda n: 4e-7 * n, stall_at=15000, stall=0.04)
    real, ra._confirmed = ra._confirmed, lambda *a: True
    try:
        assert ra.attack(seen, [("", "ab")]), "the stall no longer moves the exponent: the test proves nothing"
    finally:
        ra._confirmed = real
    # CONTROL: the confirmation passes a call that is quadratic (4 ms at 10,000 characters, 64 ms at 40,000).
    assert ra.attack(_Clocked(lambda n: 4e-11 * n * n), [("", "ab")])


def test_control_the_collection_reaches_the_patterns_the_first_scan_missed(collected):
    rows, skipped, _failed = collected
    pats = {p for _w, p, _f, _u in rows}
    assert r"git\s+add\s+(-f|--force)\b" in pats, "the _PRE_PATTERNS strings are not collected"
    from inspeximus import claude_code as cc
    stand_ins = {p for w, p, _f, _u in rows if w.endswith("[stand-in]")}
    for pat in (cc._GhComments.pattern, cc._ForcePush.pattern, core._SMUGGLED_PARAMS.pattern, cc._CMD_POS.pattern,
                core._RESTORE_INTENT, cc._RmRecursive.pattern, OLD_CHECKOUT):
        assert pat in stand_ins, "not attacked through its stand-in: %s" % pat
        assert pat not in {p for w, p, _f, _u in rows if not w.endswith("[stand-in]")}, "a reference copy is attacked"
    assert len(rows) > 300, len(rows)


# ---- R2-2: who may use the excluded patterns, across the whole package ----
GUARDED = set(EXCLUDED_NAMES) | {"_ex_parse_clause"}


def _excluded_uses(sources):
    """Every use of an excluded pattern, or of _ex_parse_clause, that is not one of the three allowed ones: the
    module-level definition in core.py, a use inside _ex_parse_clause, and the call inside derive_key. Counted by name
    anywhere in the package: an import from .core, an alias, an attribute (`core._EX_REL`), and a string that names
    one (`globals()["_EX_OF"]`, `getattr(core, "_EX_OF")`)."""
    bad = []
    for rel, src in sources.items():
        tree = ast.parse(src)
        parents = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parents[c] = n

        def inside(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return None
        for n in ast.walk(tree):
            name = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute)
                    else n.name.split(".")[-1] if isinstance(n, ast.alias)
                    else n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None)
            if name not in GUARDED:
                continue
            fn = inside(n)
            ok = rel.endswith("inspeximus/core.py") and (
                (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and fn is None
                 and isinstance(parents.get(n), ast.Assign))                       # the definition
                or (fn == "_ex_parse_clause" and name in EXCLUDED_NAMES)
                or (fn == "derive_key" and name == "_ex_parse_clause" and isinstance(parents.get(n), ast.Call)))
            if not ok:
                bad.append("%s:%d %s (%s)" % (rel, getattr(n, "lineno", 0), name, type(n).__name__))
    return bad


def _package_sources():
    out = {}
    for dp, _d, fs in os.walk(os.path.dirname(core.__file__)):
        for f in fs:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                out[p.replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    return out


def test_the_excluded_patterns_are_used_only_inside_derive_key_anywhere_in_the_package():
    src = open(core.__file__, encoding="utf-8").read()
    assert CLAUSE_SPLIT in src and src.count(CLAUSE_SPLIT) == 1
    assert _excluded_uses(_package_sources()) == []


AUDIT_A_BYPASSES = {
    # AUDIT-A R2-2: each passed the first version of the check above
    "inspeximus/_ex_user.py": "from .core import _EX_REL, _ex_parse_clause\n\ndef raw_match(text):\n"
                              "    return _EX_REL.match(text)\n",
    "inspeximus/core.py": "import re\n_EX_REL = re.compile('x')\n_ALIAS = _EX_REL\n\ndef f(t):\n    return _ALIAS.match(t)\n\n"
                          "def g(t):\n    return globals()['_EX_OF'].match(t)\n",
}


def test_control_the_walk_reports_audit_as_three_bypasses():
    bad = _excluded_uses(AUDIT_A_BYPASSES)
    assert any("_ex_user.py" in b and "_EX_REL" in b for b in bad), bad            # another module imports it
    assert any("core.py:3 _EX_REL" in b for b in bad), bad                          # a module-level alias
    assert any("core.py:9 _EX_OF (Constant)" in b for b in bad), bad               # globals()['_EX_OF']


# ---- R2-1: every re call whose pattern is not a plain literal is on this reviewed list ----
# (file, enclosing function, pattern source) -> why it cannot backtrack, or which attack covers it. A new site, or a
# changed one, fails test_every_re_call_with_a_built_pattern_is_reviewed until it is read and added here.
REVIEWED = {
    ("inspeximus/_storelink.py", "_components", "'[' + re.escape(seps) + ']+'"):
        "one character class with +: linear",
    ("inspeximus/archive.py", "_segment_rx", "re.escape(p.stem) + '\\\\.archive-\\\\d{4}-\\\\d{2}\\\\.\\\\d+' + re.escape(p.suffix) + '$'"):
        "an escaped file stem, fixed digit runs and an escaped suffix, anchored at $: linear; reads file names",
    ("inspeximus/archive.py", "segment_files", "rx.pattern[:-1] + '\\\\.tmp\\\\.\\\\d+$'"):
        "_segment_rx's pattern plus a fixed digit tail: linear",
    ("inspeximus/claude_code.py", "_LineFlag.__init__", "flag"):
        "compiled from a literal in _NO_REPLAY; collected at runtime from the stand-in and attacked",
    ("inspeximus/claude_code.py", "_LineFlag.__init__", "kw"):
        "compiled from a literal in _NO_REPLAY; collected at runtime from the stand-in and attacked",
    ("inspeximus/claude_code.py", "_PipeThen.__init__", "after"):
        "compiled from a literal in _NO_REPLAY; collected at runtime from the stand-in and attacked",
    ("inspeximus/claude_code.py", "_PipeThen.__init__", "kw"):
        "compiled from a literal in _NO_REPLAY; collected at runtime from the stand-in and attacked",
    ("inspeximus/claude_code.py", "pre_tool_use", "pat"):
        "the _PRE_PATTERNS strings: each a literal, collected by the literal pass and attacked",
    ("inspeximus/code_guard.py", "_pattern_for", "_ident(subject)"):
        "_ident: an escaped symbol between fixed lookarounds: linear",
    ("inspeximus/code_guard.py", "check_code", "_pattern_for(sym, code)"):
        "_pattern_for returns _ident(...): an escaped symbol between fixed lookarounds: linear",
    ("inspeximus/code_guard.py", "scan_lines", "_pattern_for(sym, code or '')"):
        "_pattern_for returns _ident(...): an escaped symbol between fixed lookarounds: linear",
    ("inspeximus/core.py", "<module>", "'[' + ''.join(map(chr, _RE_I_ASCII_FOLD)) + ']'"):
        "one character class: linear",
    ("inspeximus/core.py", "<module>", "'^\\\\s*(?:(?:' + '|'.join(_EX_MARK_ADV) + ')\\\\b[\\\\s]*[:,;.!-]*\\\\s*|(?:' + "
     "'|'.join(_EX_MARK_NOUN) + ')\\\\b\\\\s*[:,;-]+\\\\s*)+'"):
        "_EX_LEAD: kept at module level, collected at runtime and attacked",
    ("inspeximus/core.py", "Inspeximus._route_key", "'(?<![a-z0-9])' + re.escape(k.lower()) + '(?![a-z0-9])'"):
        "an escaped key between fixed lookarounds: linear",
    ("inspeximus/core.py", "Inspeximus._save_temps", "re.escape(base) + '\\\\.[a-z0-9_]{8}\\\\.tmp$'"):
        "an escaped file name and a fixed tail, anchored: linear; reads file names",
    ("inspeximus/core.py", "Inspeximus._save_temps", "re.escape(base) + '\\\\.rows-tmp$'"):
        "an escaped file name and a fixed tail, anchored: linear; reads file names",
    ("inspeximus/core.py", "Inspeximus._store_siblings", "Inspeximus._OWN_COPY_RE.pattern"):
        "a class attribute compiled at import: collected at runtime and attacked",
    ("inspeximus/core.py", "Inspeximus._store_siblings", "Inspeximus._SIDECAR_RE.pattern"):
        "a class attribute compiled at import: collected at runtime and attacked",
    ("inspeximus/core.py", "Inspeximus._store_siblings", "re.escape(base) + '\\\\.'"):
        "an escaped file name and a dot: linear",
    ("inspeximus/core.py", "Inspeximus.check_self_narration", "'(?<![a-z])' + re.escape(phrase) + '(?![a-z])'"):
        "an escaped phrase between fixed lookarounds: linear",
    ("inspeximus/core.py", "Inspeximus.erasure_audit",
     "'(?<![\\\\w\\\\-/:+])(?<!\\\\w\\\\.)' + re.escape(v) + '(?![\\\\w\\\\-/:+])(?!\\\\.\\\\w)'"):
        "an escaped value between fixed lookarounds: linear",
    ("inspeximus/core.py", "Inspeximus.rederive.rewrite", "re.escape(old)"):
        "an escaped literal: linear",
    ("inspeximus/core.py", "Inspeximus.route", "f'\\\\b{re.escape(str(v).lower())}\\\\b'"):
        "an escaped value between word boundaries: linear",
    ("inspeximus/core.py", "Inspeximus.verify_claim._matches", "'(?<![a-z0-9])' + re.escape(v) + '(?![a-z0-9])'"):
        "an escaped value between fixed lookarounds: linear",
    ("inspeximus/erasure_auditor.py", "TextStoreProbe.recover",
     "'(?<![a-z0-9])' + re.escape(v.lower()) + '(?![a-z0-9])'"):
        "an escaped value between fixed lookarounds: linear",
}


def test_every_re_call_with_a_built_pattern_is_reviewed():
    sites, _folded = ra.dynamic_sites()
    new = sorted(k for k in sites if k not in REVIEWED)
    gone = sorted(k for k in REVIEWED if k not in sites)
    assert not new, "re calls with a built pattern that nobody has reviewed (add each to REVIEWED with its reason):\n" + \
        "\n".join("  %s :: %s :: %s (line %d)" % (k + (sites[k],)) for k in new)
    assert not gone, "REVIEWED names sites that no longer exist: %s" % gone


AUDIT_A_PLANTED = r"""
import re
def control_literal(t):
    return re.search(r"\bdemoa\b[^\n]*?\s-zz\b", t)
def by_concat_in_function(t):
    return re.search(r"\bdemob\b" + r"[^\n]*?\s-zz\b", t)
def by_join_in_function(t):
    return re.compile("(?:" + "|".join(["democ", "demod"]) + r")\b[^\n]*?\s-zz\b").search(t)
_PAT = r"\bdemoe\b" + r"[^\n]*?\s-zz\b"
def by_global_string(t):
    return re.search(_PAT, t)
def by_format(t, kw="demof"):
    return re.search(r"\b{}\b[^\n]*?\s-zz\b".format(kw), t)
def by_fstring(t, kw="demog"):
    return re.search(rf"\b{kw}\b[^\n]*?\s-zz\b", t)
def _mk(a, b):
    return a + b
def by_helper(t):
    p = _mk(r"\bdemoh\b", r"[^\n]*?\s-zz\b")
    return re.search(p, t)
"""


def test_control_every_pattern_audit_a_planted_is_caught():
    # AUDIT-A R2-1: seven quadratic patterns ("demoX " * n: 3.0 s at 8,000 repeats). Three are attacked and flagged:
    # the plain literal, the `+` of two literals inside a function and the module-level string built by `+` (both
    # fold to literals). The four built at run time (join, format, f-string, helper) are sites nobody has reviewed.
    sites, folded = ra.dynamic_sites({"inspeximus/_evade_demo.py": AUDIT_A_PLANTED})
    unreviewed = {k[1] for k in sites if k not in REVIEWED}
    assert unreviewed == {"by_join_in_function", "by_format", "by_fstring", "by_helper"}, sites
    attacked = [p for (p, _f) in folded] + [r"\bdemoa\b[^\n]*?\s-zz\b"]
    assert len(attacked) == 3, folded
    for pat in attacked:
        rx = re.compile(pat, re.I)
        assert ra.attack(rx.search, ra.pumps(pat, re.I)), "not flagged: %s" % pat


def test_derive_key_is_linear_on_the_input_that_blows_each_excluded_pattern_up():
    """For each excluded pattern: find the input that makes it worse than linear on its own (which proves the exclusion
    is needed at all), then hand exactly that input, and the same with spaces collapsed or not, to derive_key."""
    blown = 0
    for name in EXCLUDED_NAMES:
        rx = getattr(core, name)
        r = ra.attack(rx.search, ra.pumps(rx.pattern, re.I))
        if r is None:
            continue
        blown += 1
        pre, unit = r[3]
        tabs = unit.replace(" ", "\t")
        assert ra.attack(core.derive_key, [(pre, unit), (pre, tabs)]) is None, (name, pre, unit)
    split = re.compile(CLAUSE_SPLIT)
    r = ra.attack(split.search, ra.pumps(CLAUSE_SPLIT))
    assert r, "the clause split no longer backtracks on its own: drop it from the exclusions"
    assert ra.attack(core.derive_key, [r[3], ("a", r[3][1] * 1)]) is None, r[3]
    assert blown >= 5, "fewer excluded patterns backtrack on their own than when the exclusion was written (%d)" % blown


def test_no_pattern_in_the_package_grows_worse_than_linear(collected):
    rows, _skipped, _failed = collected
    excluded = _excluded()
    worse = []
    for where, pat, fn, units in rows:
        if pat in excluded:
            continue
        r = ra.attack(fn, units or ra.pumps(pat, re.I))
        if r:
            worse.append("%s %r on %r: %s" % (where, pat[:70], r[0], [(c, round(t, 3)) for c, t in r[2]]))
    assert not worse, "\n".join(worse)
