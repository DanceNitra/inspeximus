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


# --- the background prefetch -------------------------------------------------------------


def test_a_queued_result_is_served_without_touching_the_store(provider, monkeypatch):
    """The point of the hook: after queue_prefetch, the hot path is a dictionary read.

    The store is replaced with one that refuses recall AFTER the queue has settled, so a prefetch
    that reaches it fails loudly. A test that only timed the call would pass on a fast store and
    prove nothing about whether the cache was used.
    """
    provider.handle_tool_call("inspeximus_remember", {"text": "The office is in Nitra"})
    provider.queue_prefetch("where is the office")
    provider._prefetch_thread.join(timeout=5)

    def refuse(*a, **k):
        raise AssertionError("prefetch went to the store although a result was queued")

    monkeypatch.setattr(provider._store, "recall", refuse)
    assert "Nitra" in provider.prefetch("where is the office")


def test_a_queued_result_is_not_served_for_a_different_question(provider):
    """A cache keyed on nothing answers the previous turn's question, which is the whole failure."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The office is in Nitra"})
    provider.handle_tool_call("inspeximus_remember", {"text": "The deploy window is Tuesday"})
    provider.queue_prefetch("where is the office")
    provider._prefetch_thread.join(timeout=5)
    out = provider.prefetch("when is the deploy window")
    assert "Tuesday" in out
    assert "Nitra" not in out


def test_a_queued_result_is_consumed_once(provider, monkeypatch):
    """A second turn must not be answered from the first turn's cache, even for the same words."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The office is in Nitra"})
    provider.queue_prefetch("where is the office")
    provider._prefetch_thread.join(timeout=5)
    provider.prefetch("where is the office")

    calls = []
    real = provider._store.recall
    monkeypatch.setattr(provider._store, "recall",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    assert "Nitra" in provider.prefetch("where is the office")
    assert calls, "the second prefetch was answered from a cache that should have been consumed"


def test_queue_prefetch_does_not_block_the_caller(provider, monkeypatch):
    """A queue that runs recall inline is the bug this hook exists to remove."""
    import threading
    import time

    started, release = threading.Event(), threading.Event()

    def slow(*a, **k):
        started.set()
        release.wait(timeout=5)
        return []

    monkeypatch.setattr(provider._store, "recall", slow)
    t0 = time.perf_counter()
    provider.queue_prefetch("anything at all")
    elapsed = time.perf_counter() - t0
    assert started.wait(timeout=5), "the background recall never started"
    release.set()
    provider._prefetch_thread.join(timeout=5)
    assert elapsed < 0.5, "queue_prefetch blocked for %.3fs on a recall it should not wait for" % elapsed


# --- on_pre_compress ---------------------------------------------------------------------


def test_pre_compress_hands_the_summariser_the_corrected_value_only(provider):
    """Compaction is where a retired value comes back, and the transcript cannot tell them apart."""
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The staging database is db-3.internal", "key": "staging-db"})
    provider.handle_tool_call("inspeximus_correct",
                              {"key": "staging-db", "text": "The staging database is db-7.internal"})
    out = provider.on_pre_compress([
        {"role": "user", "content": "use db-3.internal for staging"},
        {"role": "user", "content": "actually the staging database changed"},
    ])
    assert "db-7.internal" in out
    assert "db-3.internal" not in out


def test_pre_compress_returns_nothing_rather_than_an_empty_heading(provider):
    """An empty block costs tokens and reads as though memory was consulted and had nothing."""
    assert provider.on_pre_compress([]) == ""
    assert provider.on_pre_compress([{"role": "user", "content": "an unrelated question"}]) == ""


# --- on_memory_write ---------------------------------------------------------------------


def test_a_mirrored_replace_retires_the_previous_document(provider):
    """`replace` is a whole-document rewrite, so an append would leave us recalling a dropped value."""
    provider.on_memory_write("add", "user", "The user prefers metric units")
    provider.on_memory_write("replace", "user", "The user works in Bratislava")
    provider.on_memory_write("replace", "user", "The user works in Nitra")
    out = provider.prefetch("where does the user work")
    assert "Nitra" in out
    assert "Bratislava" not in out


def test_a_mirrored_remove_deletes_rather_than_demotes(provider):
    """The built-in tool removed the content, and a demoted row is still readable."""
    provider.on_memory_write("replace", "memory", "The API token is rotated on Fridays")
    assert "Fridays" in provider.prefetch("when is the token rotated")
    provider.on_memory_write("remove", "memory", "The API token is rotated on Fridays")
    assert provider.prefetch("when is the token rotated") == ""
    left = provider._store.recall("when is the token rotated", k=5, include_superseded=True)
    assert not [h for h in left if "Fridays" in h["text"]], "the removed content is still readable"


def test_a_mirrored_remove_reaches_only_rows_this_mirror_wrote(provider):
    """The mirror owns its own rows. Reaching anything else would be data loss through a side door.

    Two rows are planted that a careless predicate would take with it: one the agent stored through
    its own tool, and one carrying the mirror's exact key but a foreign source. The second is the
    one that matters, because a predicate matching on the key alone passes without it and the
    source clause would be untested decoration.
    """
    provider.handle_tool_call("inspeximus_remember",
                              {"text": "The release manager is Voss", "key": "release-manager"})
    provider._store.remember("The build server is in Kosice", key="hermes-memory::memory",
                             mtype="fact", source={"doc": "somebody-else"})
    provider.on_memory_write("replace", "memory", "The release manager is Rooke")
    provider.on_memory_write("remove", "memory", "")

    def stored(fragment):
        out = provider._store.forget(
            where=lambda r: fragment in str(r.get("text", "")), dry_run=True)
        return out["would_forget"]

    assert stored("Voss") == 1, "the removal reached a record the agent stored through its own tool"
    assert stored("Kosice") == 1, "the removal reached a foreign row that shares the mirror's key"
    assert stored("Rooke") == 0, "the mirror failed to remove its own row"


def test_the_mirror_never_raises_and_never_truncates(provider, monkeypatch):
    """A mirror that breaks the write it mirrors is worse than no mirror, and half a fact is wrong."""
    def explode(*a, **k):
        raise RuntimeError("the store is on fire")

    monkeypatch.setattr(provider._store, "remember", explode)
    provider.on_memory_write("add", "memory", "anything")        # must not raise
    monkeypatch.undo()

    # Counted through a dry-run scan of every stored record, NOT through recall. The first version
    # of this assertion asked recall for the oversized text, recall never returns it whatever the
    # code does, and the mutation control then showed the test could not fail.
    def stored(prefix):
        out = provider._store.forget(
            where=lambda r: str(r.get("text", "")).startswith(prefix), dry_run=True)
        return out["would_forget"]

    provider.on_memory_write("add", "memory", "y" * 100)
    assert stored("yyyy") == 1, "the scanner cannot see a record that was stored"   # control

    provider.on_memory_write("add", "memory", "x" * 9000)
    assert stored("xxxx") == 0, "oversized content was stored, and a truncated fact is a wrong fact"


# --- session identity and backup ---------------------------------------------------------


def test_a_session_switch_rebinds_the_source_so_forget_can_still_reach_the_writes(provider):
    """/resume and /branch reassign the id in-process, and every write carries it in its source."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The first note is about Nitra"})
    provider.on_session_switch("s2")
    provider.handle_tool_call("inspeximus_remember", {"text": "The second note is about Kosice"})

    def source_of(fragment):
        seen = []
        provider._store.forget(
            where=lambda r: (fragment in str(r.get("text", ""))
                             and seen.append((r.get("source") or {}).get("doc")) is None
                             and False),
            dry_run=True)
        return seen

    assert source_of("Nitra") == ["hermes-agent::s1"]
    assert source_of("Kosice") == ["hermes-agent::s2"], (
        "a write after the switch was attributed to the session the user left")


def test_a_session_switch_drops_a_recall_queued_for_the_old_conversation(provider):
    """Serving it into the new session would present the old one's context as this one's."""
    provider.handle_tool_call("inspeximus_remember", {"text": "The office is in Nitra"})
    provider.queue_prefetch("where is the office")
    provider._prefetch_thread.join(timeout=5)
    assert provider._prefetch_cache is not None, "nothing was queued, so this proves nothing"
    provider.on_session_switch("s2", reset=True)
    assert provider._prefetch_cache is None
    assert provider.recall_status() is None


def test_backup_paths_names_only_a_store_the_host_cannot_find(provider, tmp_path):
    """The default store is inside hermes_home, which `hermes backup` already covers."""
    from inspeximus.integrations import hermes_agent
    assert provider.backup_paths() == [], "the in-home store was declared twice to the backup"

    elsewhere = tmp_path / "outside" / "memory.json"
    p = hermes_agent.register()
    p._explicit_path = str(elsewhere)
    assert p.backup_paths() == [str(elsewhere.resolve())], (
        "a configured store outside hermes_home is invisible to the host's backup")


def test_prefetch_waits_for_an_identical_recall_instead_of_starting_a_second_one(provider, monkeypatch):
    """Two scans of one store are slower than one, so a turn that arrives early waits."""
    import threading

    calls, release = [], threading.Event()
    real = provider._store.recall

    def counted(*a, **k):
        calls.append(1)
        release.wait(timeout=5)
        return real(*a, **k)

    provider.handle_tool_call("inspeximus_remember", {"text": "The office is in Nitra"})
    monkeypatch.setattr(provider._store, "recall", counted)
    provider.queue_prefetch("where is the office")
    while not calls:                                          # the background recall has started
        pass
    threading.Timer(0.05, release.set).start()
    out = provider.prefetch("where is the office")
    provider._prefetch_thread.join(timeout=5)
    assert "Nitra" in out
    assert len(calls) == 1, "prefetch started a second recall beside the one already running"


def test_prefetch_does_not_wait_for_a_recall_of_a_different_question(provider, monkeypatch):
    """Waiting on the wrong query would add the whole background cost to this turn for nothing."""
    import threading
    import time

    release = threading.Event()
    real = provider._store.recall

    def slow(query, *a, **k):
        if "office" in query:
            release.wait(timeout=5)
        return real(query, *a, **k)

    provider.handle_tool_call("inspeximus_remember", {"text": "The deploy window is Tuesday"})
    monkeypatch.setattr(provider._store, "recall", slow)
    provider.queue_prefetch("where is the office")
    t0 = time.perf_counter()
    out = provider.prefetch("when is the deploy window")
    elapsed = time.perf_counter() - t0
    release.set()
    provider._prefetch_thread.join(timeout=5)
    assert "Tuesday" in out
    assert elapsed < 1.0, "the turn waited %.2fs on a recall for a different question" % elapsed
