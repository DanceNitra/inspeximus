"""The Hermes Agent memory provider, tested without Hermes installed.

WHY A FAKE BASE CLASS. Hermes is not a dependency and must never become one: the provider ships as a
pip entry point precisely so `pip install inspeximus` is the whole install. So the tests install a
stand-in `agent.memory_provider` module into `sys.modules`, which is what the real host provides, and
then exercise the class the adapter builds against it. That checks the two things that can actually
break in the field: whether our methods honour the contract, and whether the entry point points at
something that exists under the name a user will type.

THE CONTRACT ITSELF IS COPIED FROM THE HOST and the copy is the risk, so the fake declares only the
abstract methods and the tests assert behaviour rather than shape wherever they can.
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
    """Install a stand-in for the host module, then let the adapter build its class against it."""

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
    assert p is not None, "register() returned None while the host module was importable"
    p.initialize("s1", hermes_home=str(tmp_path))
    return p


def test_the_entry_point_name_is_the_name_the_provider_answers_to():
    """A mismatch is discoverable and unselectable, and nothing else would catch it.

    Hermes activates a provider by the entry-point name, so the name in pyproject.toml and
    PROVIDER_NAME are one fact written in two files. This reads the packaging metadata rather than
    the source, because the metadata is what the host sees.
    """
    import re
    import pathlib
    from inspeximus.integrations import hermes_agent

    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'\[project\.entry-points\."hermes_agent\.memory_providers"\]\s*\n([^\[]+)', text)
    assert m, "pyproject.toml declares no hermes_agent.memory_providers entry point"
    names = dict(re.findall(r'^\s*([\w.-]+)\s*=\s*"([^"]+)"', m.group(1), re.M))
    assert hermes_agent.PROVIDER_NAME in names, (
        "the entry point is published under %s but the provider answers to %r, so a user who writes "
        "that name into memory.provider gets nothing" % (sorted(names), hermes_agent.PROVIDER_NAME))
    assert names[hermes_agent.PROVIDER_NAME] == "inspeximus.integrations.hermes_agent:register"


def test_importing_the_adapter_does_not_import_hermes():
    """The zero-dependency promise, checked where it is easy to break: at import time."""
    for name in [n for n in sys.modules if n == "agent" or n.startswith("agent.")]:
        del sys.modules[name]
    import importlib
    from inspeximus.integrations import hermes_agent
    importlib.reload(hermes_agent)
    assert "agent.memory_provider" not in sys.modules
    assert hermes_agent.provider_class() is None, (
        "with the host absent the adapter must offer no class, so discovery cannot activate it")
    assert hermes_agent.register() is None


def test_recall_excludes_what_a_correction_retired(provider):
    """The one claim this adapter makes that a retrieval-only provider cannot: prefetch is filtered."""
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The deploy target is staging-1", "key": "deploy::target"})
    out = provider.prefetch("what is the deploy target")
    assert "staging-1" in out

    provider.handle_tool_call("inspeximus_correct",
                              {"key": "deploy::target", "text": "The deploy target is prod-7"})
    out = provider.prefetch("what is the deploy target")
    assert "prod-7" in out
    assert "staging-1" not in out, (
        "the retired value came back as context, which is the entire failure this provider exists "
        "to prevent. Got: %r" % out)


def test_revert_puts_the_previous_value_back(provider):
    """The query here overlaps the stored wording ON PURPOSE. With no embedder configured, recall is
    lexical, so "who owns the project" against "The owner is Grace" returns nothing and the test
    would be measuring the query rather than the revert. That is a real property of a default
    install, documented in the adapter, not something to hide behind a luckier phrasing."""
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The owner is Ada", "key": "project::owner"})
    provider.handle_tool_call("inspeximus_correct",
                              {"key": "project::owner", "text": "The owner is Grace"})
    assert "Grace" in provider.prefetch("project owner")

    res = json.loads(provider.handle_tool_call("inspeximus_revert", {"key": "project::owner"}))
    assert res["ok"] is True, res
    back = provider.prefetch("project owner")
    assert "Ada" in back and "Grace" not in back, back


def test_every_tool_returns_a_json_string(provider):
    """The base class requires a JSON string, including on the failure paths."""
    calls = [
        ("inspeximus_remember", {"text": "a fact"}),
        ("inspeximus_remember", {}),                       # missing required argument
        ("inspeximus_revert", {"key": "nothing::here"}),   # nothing to revert
        ("inspeximus_forget", {"subject": "nobody"}),
        ("no_such_tool", {}),
    ]
    for name, args in calls:
        out = provider.handle_tool_call(name, args)
        assert isinstance(out, str), (name, type(out))
        parsed = json.loads(out)                            # raises if it is not JSON
        assert "ok" in parsed, (name, parsed)


def test_prefetch_is_empty_rather_than_a_header_with_nothing_under_it(provider):
    """An empty block costs tokens and reads as "memory was consulted", which is a different claim."""
    assert provider.prefetch("") == ""
    assert provider.prefetch("a question about something never stored") == ""
    assert provider.recall_status() is None


def test_recall_status_reflects_only_the_last_prefetch(provider):
    """Named in the base class: it must never report a stale prior count."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The build runs on Tuesdays"})
    provider.prefetch("when does the build run")
    assert provider.recall_status() is not None
    assert provider.recall_status().count >= 1

    provider.prefetch("")
    assert provider.recall_status() is None, "a prior count survived a prefetch that recalled nothing"


def test_two_profiles_do_not_share_one_store(hermes_stub, tmp_path):
    """hermes_home is profile-scoped, and writing both profiles into one file is the isolation bug."""
    from inspeximus.integrations import hermes_agent
    a, b = hermes_agent.register(), hermes_agent.register()
    a.initialize("s", hermes_home=str(tmp_path / "profileA"))
    b.initialize("s", hermes_home=str(tmp_path / "profileB"))
    a.handle_tool_call("inspeximus_remember", {"text": "Profile A drinks tea"})
    assert "tea" in a.prefetch("what does the profile drink")
    assert "tea" not in b.prefetch("what does the profile drink")


def test_is_available_makes_no_network_call(provider, monkeypatch):
    """The base class says so explicitly, and a provider that dials out here stalls agent startup."""
    import socket

    def refuse(*a, **k):
        raise AssertionError("is_available() opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert provider.is_available() is True
