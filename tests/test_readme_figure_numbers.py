"""The README's opening figure publishes numbers, and an image is where a text scanner cannot see them.

This is the tests-badge defect one step further out. `claims_audit.scan_numbers` deliberately skips
URLs, which is how `tests-2793` sat on the most-read line of the README with no gate over it for
months. An SVG is worse: the whole file is invisible to a markdown scanner, so a figure can drift from
the table three inches below it and nothing anywhere reports a difference.

There are also TWO of them -- a light and a dark variant -- which means two files carrying one truth,
and the failure mode where a reader on a dark theme is shown a different number than a reader on a
light one. Nothing else in the repository would ever notice that.

So: the figure's numbers are read out of the SVG and required to equal the ones the registry already
publishes, both variants are required to agree with each other, and both controls below fail loudly
if either check stops looking.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import claims_audit as ca  # noqa: E402

VARIANTS = ("docs/assets/correction-light.svg", "docs/assets/correction-dark.svg")

#: What the figure is allowed to say, read from the registry rather than retyped here. These are the
#: rows that already own these tokens on README.md, so the figure cannot become a second source.
FIGURE_CLAIM_IDS = ("readme-echo-ours", "readme-echo-graphiti", "readme-echo-mem0",
                    "readme-echo-control", "readme-echo-trials")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _percents(svg_text):
    """Every percentage the figure prints, in order."""
    return [m.group(1) for m in re.finditer(r">(\d+(?:\.\d+)?)%<", svg_text)]


def _registered_tokens():
    out = set()
    for c in ca.NUMBER_CLAIMS:
        if c["id"] in FIGURE_CLAIM_IDS:
            out |= set(c["tokens"])
    assert out, (
        "no registry rows matched %r -- the ids were renamed and this test is now comparing the figure "
        "against an EMPTY set, which every number passes" % (FIGURE_CLAIM_IDS,)
    )
    return out


@pytest.mark.parametrize("rel", VARIANTS)
def test_the_figure_exists_and_is_referenced_by_the_readme(rel):
    assert os.path.exists(os.path.join(ROOT, rel)), f"{rel} is missing; the README points at it"
    assert rel in _read("README.md"), (
        f"{rel} is not referenced by README.md -- a variant nothing points at is a variant nobody "
        f"maintains, and it will drift from the one that is shown")


@pytest.mark.parametrize("rel", VARIANTS)
def test_every_number_in_the_figure_is_one_the_registry_already_publishes(rel):
    """A figure is a surface. Ours may not introduce a number that no command reproduces."""
    allowed = _registered_tokens()
    found = _percents(_read(rel))
    assert len(found) >= 4, (
        f"read {len(found)} percentages out of {rel} -- the extraction stopped matching, and a guard "
        f"reading nothing reports SAFE")
    unknown = sorted({p for p in found if p not in allowed})
    assert not unknown, (
        f"{rel} publishes {unknown}, which no registry row owns. Register it in claims_audit.py or "
        f"correct the figure; an image is not a place to keep an unbacked number.")


def test_the_two_variants_tell_the_same_story():
    """Two files, one truth. A reader on a dark theme must not be shown a different measurement."""
    light, dark = (_percents(_read(v)) for v in VARIANTS)
    assert light == dark, (
        f"the light figure says {light} and the dark one says {dark} -- whichever is wrong, half our "
        f"readers are being shown it")
    assert len(set(light)) >= 3, (
        f"only {len(set(light))} distinct values in the figure; it is no longer showing a comparison")


def test_the_figure_states_its_sample_size():
    """A bar chart without n is a shape, not a measurement."""
    for rel in VARIANTS:
        assert re.search(r"n = 30", _read(rel)), f"{rel} does not state the sample size"


def test_the_zero_bar_is_drawn_and_not_merely_absent():
    """0% is our headline, and an empty bar is indistinguishable from a rendering bug.

    Checked because it is the one value where 'nothing drawn' and 'the correct answer' look identical,
    which is the same confusion this whole file exists to prevent, in pixels instead of numbers.
    """
    for rel in VARIANTS:
        text = _read(rel)
        assert ">0%<" in text, f"{rel} no longer prints the 0% label"
        # the origin tick: a rect of width 3 at the bar's left edge
        assert re.search(r'width="3"\s+height="26"', text), (
            f"{rel} draws no origin marker for the 0% row, so it reads as a missing bar")


# ── CONTROLS: each check must be seen to fail ───────────────────────────────────────────────────────
def test_CONTROL_a_figure_number_the_registry_does_not_own_is_caught():
    allowed = _registered_tokens()
    tampered = _read(VARIANTS[0]).replace(">46.7%<", ">4.7%<")
    assert tampered != _read(VARIANTS[0]), "fixture no longer reproduces: the mem0 label changed"
    assert [p for p in _percents(tampered) if p not in allowed] == ["4.7"], (
        "a figure number outside the registry was not detected")


def test_CONTROL_variants_that_disagree_are_caught():
    light = _percents(_read(VARIANTS[0]))
    drifted = _percents(_read(VARIANTS[1]).replace(">13.3%<", ">31.3%<"))
    assert light != drifted, "a divergence between the two variants was not detected"


def test_CONTROL_an_unreadable_figure_does_not_read_as_clean():
    """If the extraction stops matching, the count floor must fire rather than an empty pass."""
    assert _percents("<svg><text>no numbers here</text></svg>") == []
