"""Round seven: a path regression of mine, and the first deliberate attacker-model pass.

Seven rounds hunted correctness. This one finally asked "what can someone who can WRITE to the store, but does
not hold receipt_key, actually do" — and the sharpest answer was not a bug but a false CLAIM: `state_digest`
said "any out-of-band edit changes the digest" while being blind to the two fields that decide which record
recall returns first.

Where a limit is inherent, the fix is the claim. Where it is a mistake, the fix is the code. Both are here.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── the path regression 1.64.0 introduced ───────────────────────────────────────────────────────────
def test_an_os_pathlike_store_path_still_works():
    """1.64.0 added expanduser via `str(path)`, and `str()` REPR's an os.PathLike into
    `<object at 0x...>` — so the store went to a junk-named file (or on POSIX to a real one nobody meant).
    `Path(x)` had honoured `__fspath__` correctly before. os.fspath is the right coercion."""
    class Like:
        def __init__(self, p):
            self.p = p

        def __fspath__(self):
            return self.p

    target = _path()
    m = Inspeximus(path=Like(target))
    m.remember("x")
    m.flush()
    assert os.path.exists(target), f"wrote to {m.path} instead"


def test_a_pathlib_path_still_works():
    target = _path()
    m = Inspeximus(path=pathlib.Path(target))
    m.remember("y")
    m.flush()
    assert os.path.exists(target)


def test_a_bytes_path_raises_rather_than_writing_somewhere_odd():
    """`str(b'/tmp/x.json')` is `"b'/tmp/x.json'"` — a real, wrong filename. A TypeError is the honest
    outcome for a type the library does not accept."""
    with pytest.raises(TypeError):
        Inspeximus(path=b"/tmp/inspeximus_bytes_probe.json")


def test_a_tilde_in_the_middle_of_a_path_is_not_expanded():
    """expanduser only touches a LEADING ~. A path like /tmp/~cache/m.json must stay literal, or the store
    silently lands somewhere the caller did not name."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "~cache", "m.json")
    m = Inspeximus(path=p)
    m.remember("z")
    m.flush()
    assert os.path.exists(p), f"resolved to {m.path}"


# ── the claim that was false ────────────────────────────────────────────────────────────────────────
def test_state_digest_documents_its_blind_spot():
    """It said "any supersession, revert, erasure, or out-of-band edit changes the digest". Measured: a
    tamper of `value`, and a `credit()` call, both leave the digest identical — and those are exactly what
    RANKING uses, so they decide which record recall returns first.

    The mechanism cannot simply be widened: `recall()` bumps `value` and `last_access`, so a digest covering
    them would change on every READ and no witness could ever match. The claim is what had to change."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("a fact")
    before = m.state_digest()

    next(r for r in m.items if r["id"] == rid)["value"] = 1e9
    assert m.state_digest() == before, "if this ever changes, update the docstring — do not delete this test"
    m.credit([rid], outcome=True, weight=500)
    assert m.state_digest() == before

    doc = Inspeximus.state_digest.__doc__ or ""
    assert "HONEST SCOPE" in doc, "the blind spot must be stated where a reader looks"
    assert "OUTSIDE the digest" in doc and "credit()" in doc, doc[:200]
    # Assert what the docstring CLAIMS, not which words it contains: it quotes the old false sentence
    # deliberately, to record what was wrong. My first version of this assertion searched for the string and
    # tripped over that quotation — the same mistake as grepping a file for a name its comment mentions.
    assert "which was false" in doc, "the old claim must be marked as false where it is quoted"


def test_state_digest_still_moves_on_what_it_does_cover():
    """A scope note is only honest if the covered set genuinely IS covered."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("a fact", key="k", object="1")
    before = m.state_digest()

    next(r for r in m.items if r["id"] == rid)["text"] = "an edited fact"
    assert m.state_digest() != before, "a text edit must change the digest"

    m2 = Inspeximus(path=_path(), receipts=True)
    m2.remember("v1", key="k", object="1")
    d = m2.state_digest()
    m2.remember("v2", key="k", object="2")            # supersession
    assert m2.state_digest() != d


# ── the attacker-model limit that had to be disclosed ───────────────────────────────────────────────
def test_supersession_is_unauthenticated_and_says_so():
    """It branches on tenant, valid_from, object and asserts_change — never on WHO wrote. Anyone who can
    call remember() and knows the key retires the current value. That is ordinary last-write-wins, but the
    asymmetry matters: revert() is capability-gated and the write path that achieves the same outcome is
    not. Making it authenticated is a design change; stating it is the minimum."""
    m = Inspeximus(path=_path())
    m.remember("Payout wallet is 0xTRUE", key="payout::wallet", object="0xTRUE",
               source={"doc": "finance.internal"})
    m.remember("Payout wallet is 0xEVIL", key="payout::wallet", object="0xEVIL",
               source={"doc": "evil.example"})

    assert [h["text"] for h in m.recall("payout wallet")][:1] == ["Payout wallet is 0xEVIL"]
    doc = Inspeximus._supersede_by_key.__doc__ or ""
    assert "UNAUTHENTICATED" in doc
    assert "trusted_only" in doc, "the mitigation must be named next to the limit"


def test_the_documented_mitigation_does_exactly_what_is_claimed_and_no_more():
    """A disclosed limit whose mitigation is overstated is worse than the limit.

    My first version of this test asserted only "no 0xEVIL in the result" — which passes TRIVIALLY when the
    result is empty, and it is: the attacker's write RETIRES the true record, so a trusted-only recall
    returns nothing at all. The guarantee is "you will not be told the attacker's answer", NOT "you will be
    told the right one", and the docstring now says so."""
    m = Inspeximus(path=_path())
    m.trust_seeds = {Inspeximus._canon_source("finance.internal")}
    m.remember("Payout wallet is 0xTRUE", key="payout::wallet", object="0xTRUE",
               source={"doc": "finance.internal"})
    m.remember("Payout wallet is 0xEVIL", key="payout::wallet", object="0xEVIL",
               source={"doc": "evil.example"})

    trusted = m.recall("payout wallet", trusted_only=True)
    assert not any("0xEVIL" in h["text"] for h in trusted), "the poison must not be served"
    assert trusted == [], "and the true value is NOT served either — the attacker retired it"

    with_history = m.recall("payout wallet", trusted_only=True, include_superseded=True)
    assert any("0xTRUE" in h["text"] for h in with_history), "the truth survives, but only as history"

    doc = Inspeximus._supersede_by_key.__doc__ or ""
    assert "PARTIAL" in doc and "include_superseded=True" in doc,         "the mitigation's exact shape must be stated, not just its name"


def test_trusted_only_fails_closed_with_no_trust_root():
    """The mitigation must not silently degrade to 'return everything' when nobody configured a root."""
    m = Inspeximus(path=_path())
    m.remember("anything", source={"doc": "whoever"})
    assert m.recall("anything", trusted_only=True) == []
