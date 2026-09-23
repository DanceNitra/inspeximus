"""`remember(raise_on_block=True)` raises on a write a guard retired; the default does not change.

Crew OS, 2026-09-21: an objectless rewrite of one layer returned an id four times while the old value
stayed current. `last_write` said so, and the caller did not read it. The opt-in makes the refusal
impossible to miss without changing the return type every existing caller depends on.

The controls: the default path must still return a plain id and raise nothing, a write that lands
must not raise with the flag on, and the raise must cover every guard that blocks (objectless and
echo here), not only the one that was reported.
"""
from __future__ import annotations

import json
import os

import pytest

from inspeximus import Inspeximus, WriteBlocked


def _store(tmp_path):
    return Inspeximus(str(tmp_path / "m.json"))


def test_an_objectless_rewrite_raises_with_the_policy_and_the_current_id(tmp_path):
    m = _store(tmp_path)
    first = m.remember("v1 text", key="crew::layer", object="v1")
    with pytest.raises(WriteBlocked) as e:
        m.remember("v2 text without object", key="crew::layer", raise_on_block=True)
    assert e.value.policy == "objectless_guard"
    assert e.value.current_id == first
    assert e.value.verdict["blocked"] is True
    assert m.current("crew::layer")["id"] == first


def test_the_blocked_record_is_saved_before_the_raise(tmp_path):
    """Nothing is rolled back: the record the exception names is on disk and in history."""
    m = _store(tmp_path)
    m.remember("v1 text", key="crew::layer", object="v1")
    with pytest.raises(WriteBlocked) as e:
        m.remember("v2 text without object", key="crew::layer", raise_on_block=True)
    reopened = Inspeximus(str(tmp_path / "m.json"))
    assert any(r["id"] == e.value.id for r in reopened.history("crew::layer"))


def test_an_echo_of_a_superseded_value_raises_too(tmp_path):
    """The raise sits on the verdict, not on one guard, so the echo guard reaches it as well."""
    m = _store(tmp_path)
    m.remember("the port is 8080", key="svc::port", object="8080")
    m.remember("the port is 9090", key="svc::port", object="9090")
    with pytest.raises(WriteBlocked) as e:
        m.remember("the port is 8080", key="svc::port", object="8080", raise_on_block=True)
    assert e.value.policy == "echo_guard"


def test_CONTROL_the_default_returns_an_id_and_raises_nothing(tmp_path):
    m = _store(tmp_path)
    m.remember("v1 text", key="crew::layer", object="v1")
    rid = m.remember("v2 text without object", key="crew::layer")
    assert isinstance(rid, str) and m.last_write["blocked"] is True


def test_CONTROL_a_write_that_lands_does_not_raise_with_the_flag_on(tmp_path):
    m = _store(tmp_path)
    m.remember("v1 text", key="crew::layer", object="v1")
    rid = m.remember("v2 text", key="crew::layer", object="v2", raise_on_block=True)
    assert m.current("crew::layer")["id"] == rid and m.last_write["blocked"] is False


def test_CONTROL_a_keyless_write_never_raises(tmp_path):
    m = _store(tmp_path)
    assert isinstance(m.remember("a plain note", raise_on_block=True), str)
