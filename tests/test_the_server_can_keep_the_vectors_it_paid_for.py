"""A server configured with an embedder could not keep the vectors it computed.

`open_store` takes `persist_vectors`, the MCP server never passed it, and no environment variable
set it. So every server with `INSPEXIMUS_EMBED_URL` ran with a RAM-only index: one embedding call per
record on every open, thrown away at exit, paid again on the next start. On our own 619-record store
that is 619 network calls per restart for an index that never reaches disk.

The store's own report already said so and nobody was listening. `index_coherence` returns the note
"persist_vectors=False: vectors are a RAM-only cache rebuilt per process", and `reembed` returns a
warning with the same content and the remedy in it: "Open the store with persist_vectors=True to keep
them." The remedy named a constructor argument the server did not expose.

Default stays off, so a store written before this is byte-identical to one written after.
"""
from __future__ import annotations

import pytest

from inspeximus.mcp_server import _flag_from_env


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_documented_true_spellings_all_enable_it(value):
    assert _flag_from_env("X", {"X": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_everything_else_leaves_it_off(value):
    """CONTROL. The failure worth preventing is a flag that reads as on for any non-empty string,
    which would switch on persistence for a server whose operator wrote INSPEXIMUS_PERSIST_VECTORS=0.
    """
    assert _flag_from_env("X", {"X": value}) is False


def test_an_absent_variable_is_off():
    assert _flag_from_env("X", {}) is False


def test_it_reads_the_same_spellings_as_the_flags_that_came_before_it():
    """The two existing flags parse this way. A third that parsed differently would be a trap."""
    for v in ("1", "true", "yes", "on"):
        assert _flag_from_env("INSPEXIMUS_RECEIPTS", {"INSPEXIMUS_RECEIPTS": v}) is True
        assert _flag_from_env("INSPEXIMUS_PERSIST_VECTORS", {"INSPEXIMUS_PERSIST_VECTORS": v}) is True


def test_the_server_hands_the_flag_to_the_store():
    """The parser is worthless if its result never reaches `open_store`.

    This is the half that was missing before: the argument existed, the server simply never passed
    it. Reading the call site is the only way to tell a wired flag from a parsed one.
    """
    import inspect

    from inspeximus import mcp_server

    src = inspect.getsource(mcp_server)
    call = src.split("_MEM = open_store(")[1].split(")\n")[0]
    assert "persist_vectors=" in call, (
        "open_store is called without persist_vectors, so the flag changes nothing: %r" % call)


def test_the_pii_flag_reaches_the_store_too():
    """The same gap, found while closing the first one.

    The store takes `pii_detect` and the server had no way to set it, so `pii_report` on a
    server-backed store counted a column nothing ever filled: zero exposure over a store where
    nobody had looked. A report that cannot be wrong is the shape this project keeps finding.
    """
    import inspect

    from inspeximus import mcp_server

    call = inspect.getsource(mcp_server).split("_MEM = open_store(")[1].split(")\n")[0]
    assert "pii_detect=" in call, (
        "open_store is called without pii_detect, so the flag changes nothing: %r" % call)


def test_both_new_flags_default_to_off():
    """Neither may change a store that was written before them.

    `pii_detect` especially: the tag is stamped at write time and `forget_pii()` hard-deletes every
    record carrying one, so a default-on flag would change what a later sweep removes.
    """
    assert _flag_from_env("INSPEXIMUS_PERSIST_VECTORS", {}) is False
    assert _flag_from_env("INSPEXIMUS_PII_DETECT", {}) is False
