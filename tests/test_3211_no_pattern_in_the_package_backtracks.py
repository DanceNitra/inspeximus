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


def test_the_excluded_patterns_are_used_only_inside_derive_key():
    src = open(core.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    users = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Name) and n.id in EXCLUDED_NAMES:
                    users.setdefault(n.id, set()).add(fn.name)
    assert users and all(v == {"_ex_parse_clause"} for v in users.values()), users
    callers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
               and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_ex_parse_clause" for c in ast.walk(fn))}
    assert callers == {"derive_key"}, callers
    assert CLAUSE_SPLIT in src and src.count(CLAUSE_SPLIT) == 1


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
