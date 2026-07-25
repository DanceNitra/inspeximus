"""The last three findings from round four's sweep, all of the same class: a control that failed OPEN.

Each one silently granted what it exists to withhold — agreement between two different values, a spend against
an unknown balance, and an anchor that looks witnessed when nobody signed it. A control that fails open is
worse than no control, because its presence is what stops anyone looking.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _sig(value):
    return Inspeximus._obj_sig({"object": value})


# ── the value signature erased whole writing systems ────────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("東京", "北京"),        # Tokyo / Beijing in han
    ("日本語", "한국어"),   # Japanese / Korean
    ("Москва", "Киев"),   # Moscow / Kyiv in cyrillic
])
def test_two_different_non_latin_values_do_not_share_a_signature(a, b):
    """`[^a-z0-9]` deleted every non-Latin character, so both values normalised to the EMPTY STRING and
    compared equal. `observe()` then recorded a flat contradiction as agreement AND marked its support seen,
    so later corrections were discounted."""
    assert _sig(a) != _sig(b)
    assert _sig(a) and _sig(b), "a value must never normalise to nothing"


def test_observe_treats_a_non_latin_contradiction_as_a_contradiction():
    m = Inspeximus(path=_path())
    m.remember("city", key="city", object="東京")
    assert m.observe("city", key="city", object="北京").get("agreed") is not True


def test_observe_still_recognises_genuine_agreement_in_any_script():
    """The counterpart: a signature change that stopped agreeing with itself would be just as wrong."""
    for value in ("東京", "Tokyo"):
        m = Inspeximus(path=_path())
        m.remember("city", key="city", object=value)
        assert m.observe("city", key="city", object=value).get("agreed") is True


def test_punctuation_still_folds():
    """Normalising away punctuation was the point of the signature; only the script-erasure was the defect."""
    assert _sig("3-2") == _sig("3/2")
    assert _sig("Tokyo-JP") == _sig("tokyo jp")


# ── the lifetime budget reset itself on a corrupt file ──────────────────────────────────────────────
def test_an_unreadable_budget_refuses_the_spend_instead_of_zeroing_it():
    """Its own docstring: "a patient attacker must not reset its spent budget by spanning sessions." A
    corrupt sidecar reset the state to {}, so a 0.9 spend against a 1.0 budget was allowed a SECOND time —
    cumulative 1.8 — and nothing reported it. Corrupting one file was the reset."""
    p = _path()
    m = Inspeximus(path=p)
    rid = m.remember("x", source={"doc": "src"})
    assert m.spend_irreversible([rid], amount=0.9, budget=1.0)["allowed"] is True

    open(p + ".irrev.json", "w", encoding="utf-8").write("{corrupt")
    reopened = Inspeximus(path=p)
    with pytest.raises(RuntimeError, match="unreadable"):
        reopened.spend_irreversible([rid], amount=0.9, budget=1.0)


def test_a_missing_budget_file_is_not_an_error():
    """An empty budget is the correct starting state; only an UNREADABLE one is a refusal."""
    m = Inspeximus(path=_path())
    rid = m.remember("x", source={"doc": "src"})
    assert m.spend_irreversible([rid], amount=0.5, budget=1.0)["allowed"] is True


def test_the_budget_still_stops_a_real_overspend():
    p = _path()
    m = Inspeximus(path=p)
    rid = m.remember("x", source={"doc": "src"})
    m.spend_irreversible([rid], amount=0.9, budget=1.0)
    again = Inspeximus(path=p).spend_irreversible([rid], amount=0.9, budget=1.0)
    assert again["allowed"] is False


# ── an anchor that looked witnessed ─────────────────────────────────────────────────────────────────
def test_a_failing_witness_signer_does_not_yield_a_silently_unsigned_anchor():
    """It returned a dict byte-identical to `anchor()` with no signer at all, so the caller who asked for
    external witnessing — the ONLY operator-adversarial property in the whole design — could not tell it had
    not happened."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("y")
    with pytest.raises(RuntimeError, match="witness signer"):
        m.anchor(sign=lambda b: 1 / 0)


def test_a_working_signer_still_signs_and_an_unsigned_anchor_is_still_allowed():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("y")
    assert "witness_sig" in m.anchor(sign=lambda b: b.hex()[:16])
    assert "witness_sig" not in m.anchor()          # not asking for one remains fine
