"""Prefetch must name the key, because every correction tool takes a key and nothing else does.

MEASURED 2026-10-28 while someone dogfooded the provider inside Hermes. The store held
`deploy::target`, `recall` returned it, and the context line read `- The deploy target is prod-7`.
The agent then said it could see the fact but could not say which key held it, so
`inspeximus_correct` and `inspeximus_revert`, which take a key and will not guess, were
unreachable from the only place the agent learns what is in the store.

The controls matter here: a key-less record must still appear, and the text must still be filtered
by supersession, or this file would pass over a provider that had stopped recalling at all.
"""
from __future__ import annotations

import json
import sys
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pytest


@pytest.fixture()
def hermes_stub(monkeypatch):
    @dataclass(frozen=True)
    class RecallStatus:
        provider_label: str
        count: int
        glyph: str = "*"

    class MemoryProvider(ABC):
        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs) -> None: ...

        @abstractmethod
        def get_tool_schemas(self): ...

    pkg = types.ModuleType("agent")
    mod = types.ModuleType("agent.memory_provider")
    mod.MemoryProvider = MemoryProvider
    mod.RecallStatus = RecallStatus
    pkg.memory_provider = mod
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", mod)
    return mod


@pytest.fixture()
def provider(hermes_stub, tmp_path):
    from inspeximus.integrations import hermes_agent
    p = hermes_agent.register()
    assert p is not None
    p.initialize("s1", hermes_home=str(tmp_path))
    return p


def test_a_keyed_record_arrives_with_its_key(provider):
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The deploy target is prod-7", "key": "deploy::target"})
    out = provider.prefetch("what is the deploy target")
    assert "prod-7" in out, out
    assert "deploy::target" in out, (
        "the context names no key, so the agent cannot call inspeximus_correct or "
        "inspeximus_revert, which take a key and nothing else. Got: %r" % out)


def test_the_key_a_correction_needs_is_the_one_prefetch_names(provider):
    """End to end: read the key out of the context, then correct with exactly that string."""
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The owner is Ada", "key": "project::owner"})
    out = provider.prefetch("project owner")
    keys = [tok for tok in out.replace("\n", " ").split() if "::" in tok]
    assert keys, "no key-shaped token in the context: %r" % out
    key = keys[0].strip(":,.()[]")
    res = json.loads(provider.handle_tool_call(
        "inspeximus_correct", {"key": key, "text": "The owner is Grace"}))
    assert res.get("ok") is True, res
    after = provider.prefetch("project owner")
    assert "Grace" in after and "Ada" not in after, after


def test_a_record_with_no_key_still_arrives(provider):
    """Control. A key-less record must not be dropped by the line that adds the key."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The build runs nightly at 02:00"})
    out = provider.prefetch("when does the build run")
    assert "nightly" in out, out


def test_a_correction_still_hides_the_retired_text(provider):
    """Control. If this fails, the recall itself broke and the two assertions above prove nothing."""
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The deploy target is staging-1", "key": "deploy::target"})
    provider.handle_tool_call("inspeximus_correct",
                              {"key": "deploy::target", "text": "The deploy target is prod-7"})
    out = provider.prefetch("what is the deploy target")
    assert "prod-7" in out and "staging-1" not in out, out
