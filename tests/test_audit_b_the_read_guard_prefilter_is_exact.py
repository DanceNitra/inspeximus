"""AUDIT-B B-05: the read guard's word pre-check changes the cost, never the verdict.

`_instruction_shape` skips a pattern when the text lacks a word the pattern requires, and `_stuffing`
counts with `collections.Counter`. Both must give exactly what the regex-only and dict-counting versions
gave, because a verdict that differs here is either a quarantine bypass or a false quarantine.

Three kinds of evidence:
  * the fold table is complete: every code point that `re.IGNORECASE` matches to an ASCII letter folds
    to that letter, and nothing else matches the non-letters the required words contain;
  * a differential against the reference implementations, over the guard's attack corpus, every string
    literal in the test suite that holds a required word, and case and fold variants of each;
  * controls: every shape is exercised by the corpus, and the pre-check is consulted for every shape.

The same differential was run over every text of two real stores (78,099 texts) before this shipped.
"""
import ast
import collections
import glob
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import inspeximus.core as core  # noqa: E402


def _shape_reference(text):
    t = text or ""
    return [name for name, rx in core._INSTRUCTION_SHAPES if rx.search(t)]


def _stuffing_reference(text):
    words = core._STUFF_WORDS.findall((text or "").lower())
    n = len(words)
    if n < 12:
        return None
    counts = {}
    for w in words:
        if len(w) >= 3 and w not in core._STUFF_STOP:
            counts[w] = counts.get(w, 0) + 1
    if not counts:
        return None
    w, c = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    share = c / n
    if c >= 4 and share >= 0.12:
        return {"word": w, "count": c, "share": round(share, 3), "words": n}
    return None


def _all_code_points():
    return (chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)


def test_the_fold_table_covers_every_character_ignorecase_maps_to_an_ascii_letter():
    by_class = re.compile("[a-z]", re.I)
    by_literal = re.compile("|".join("abcdefghijklmnopqrstuvwxyz"), re.I)
    hits = {ch for ch in _all_code_points() if by_class.fullmatch(ch) or by_literal.fullmatch(ch)}
    non_ascii = sorted(ch for ch in hits if ord(ch) > 127)
    assert len(hits) == 52 + len(non_ascii)
    for ch in non_ascii:
        letters = [x for x in "abcdefghijklmnopqrstuvwxyz" if re.fullmatch(x, ch, re.I)]
        folded = ch.translate(core._RE_I_ASCII_FOLD).lower()
        assert letters == [folded], (hex(ord(ch)), letters, folded)
    assert sorted(ord(c) for c in non_ascii) == sorted(core._RE_I_ASCII_FOLD), non_ascii


def test_no_other_character_matches_a_non_letter_in_the_required_words():
    punct = sorted({ch for groups in core._SHAPE_REQUIRES.values() for g in groups for w in g
                    for ch in w if not ch.isalpha()})
    rx = re.compile("[" + re.escape("".join(punct)) + "]", re.I)
    assert sorted(ch for ch in _all_code_points() if rx.fullmatch(ch)) == punct


def _corpus():
    import test_two_read_guards_quarantine_instructions_and_demote_stuffing as g
    base = list(g.GENUINE) + [g.STUFFED, g.INJECTION] + [t for t, _ in g.PARAPHRASED] + list(g.STILL_ORDINARY)
    words = {w for groups in core._SHAPE_REQUIRES.values() for grp in groups for w in grp}
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 8 <= len(node.value) <= 4000:
                low = node.value.lower()
                if any(w in low for w in words):
                    base.append(node.value)
    folds = [("i", "ı"), ("i", "İ"), ("s", "ſ"), ("k", "K"), ("I", "İ")]
    out = []
    for t in dict.fromkeys(base):
        out += [t, t.upper(), t.title(), t.swapcase()]
        out += [t.replace(a, b) for a, b in folds]
        out.append(t.replace(" ", " "))
    return list(dict.fromkeys(out))


CORPUS = _corpus()


def test_the_verdicts_equal_the_reference_on_the_whole_corpus():
    assert len(CORPUS) > 1000, f"corpus too small to mean anything: {len(CORPUS)}"
    bad = [t for t in CORPUS if core._instruction_shape(t) != _shape_reference(t)]
    assert bad == [], f"{len(bad)} shape verdicts differ, first: {bad[:3]!r}"
    bad = [t for t in CORPUS if core._stuffing(t) != _stuffing_reference(t)]
    assert bad == [], f"{len(bad)} stuffing verdicts differ, first: {bad[:3]!r}"


def test_control_every_shape_is_exercised_and_the_precheck_is_consulted(monkeypatch):
    found = collections.Counter(s for t in CORPUS for s in _shape_reference(t))
    missing = [name for name, _ in core._INSTRUCTION_SHAPES if found[name] == 0]
    assert missing == [], f"the corpus never matches {missing}, so it proves nothing for them"
    assert set(core._SHAPE_REQUIRES) == {name for name, _ in core._INSTRUCTION_SHAPES}
    for name in core._SHAPE_REQUIRES:
        broken = dict(core._SHAPE_REQUIRES)
        broken[name] = (("\x00never-present\x00",),)
        monkeypatch.setattr(core, "_SHAPE_REQUIRES", broken)
        assert any(core._instruction_shape(t) != _shape_reference(t) for t in CORPUS), (
            f"an impossible requirement for {name} changed nothing, so the pre-check is not consulted")
    monkeypatch.undo()
