# -*- coding: utf-8 -*-
"""`InspeximusMemoryService` — an ADK memory service, tested where it is easy to get wrong.

ADK ships no conformance suite for `BaseMemoryService`, so these tests plus `adk_audit.py` are the
whole safety net. They cover the two failures the audit caught (re-ingestion duplicated a session,
and the incremental write path was missing) and the isolation guarantees a multi-user service has to
keep. Skipped entirely when google-adk is not installed, since it is an optional extra.
"""
import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

pytest.importorskip("google.adk", reason="google-adk is an optional extra")

from google.adk.events.event import Event                      # noqa: E402
from google.adk.memory.memory_entry import MemoryEntry         # noqa: E402
from google.adk.sessions.session import Session                # noqa: E402
from google.genai import types as gt                           # noqa: E402

from inspeximus.integrations.google_adk import InspeximusMemoryService   # noqa: E402


def ev(text, author="user", eid=None):
    e = Event(author=author, content=gt.Content(role=author, parts=[gt.Part(text=text)]))
    if eid:
        e.id = eid
    return e


def sess(app, user, sid, texts):
    return Session(app_name=app, user_id=user, id=sid, events=[ev(t) for t in texts])


def svc(tmp_path, name="m.json", **kw):
    return InspeximusMemoryService(path=str(tmp_path / name), **kw)


def run(coro):
    return asyncio.run(coro)


async def _texts(s, app, user, query):
    r = await s.search_memory(app_name=app, user_id=user, query=query)
    return [" ".join(p.text for p in m.content.parts if p.text) for m in r.memories]


def test_ingesting_the_same_session_twice_stores_it_once(tmp_path):
    """BaseMemoryService: "A session may be added multiple times during its lifetime"."""
    s = svc(tmp_path)
    session = sess("app", "u", "s1", ["the meeting is at noon"])
    run(s.add_session_to_memory(session))
    run(s.add_session_to_memory(session))
    run(s.add_session_to_memory(session))
    got = run(_texts(s, "app", "u", "meeting"))
    assert len([t for t in got if "noon" in t]) == 1


def test_a_growing_session_ingests_only_the_new_turns(tmp_path):
    s = svc(tmp_path)
    session = sess("app", "u", "s1", ["I live in Nitra"])
    run(s.add_session_to_memory(session))
    session.events.append(ev("I work as a plumber"))
    run(s.add_session_to_memory(session))
    got = run(_texts(s, "app", "u", "Nitra plumber"))
    assert len([t for t in got if "Nitra" in t]) == 1
    assert any("plumber" in t for t in got)


def test_event_deltas_are_supported_and_idempotent(tmp_path):
    s = svc(tmp_path)
    e = ev("the wifi password is hunter2", eid="e1")
    run(s.add_events_to_memory(app_name="app", user_id="u", events=[e], session_id="s1"))
    run(s.add_events_to_memory(app_name="app", user_id="u", events=[e], session_id="s1"))
    got = run(_texts(s, "app", "u", "wifi password"))
    assert len([t for t in got if "hunter2" in t]) == 1


def test_deltas_are_not_treated_as_the_whole_session(tmp_path):
    """"Implementations should treat `events` as an incremental update (delta)" — so nothing is dropped."""
    s = svc(tmp_path)
    run(s.add_events_to_memory(app_name="app", user_id="u", events=[ev("first fact: the sky is blue")],
                               session_id="s1"))
    run(s.add_events_to_memory(app_name="app", user_id="u", events=[ev("second fact: the grass is green")],
                               session_id="s1"))
    got = run(_texts(s, "app", "u", "fact"))
    assert any("sky" in t for t in got) and any("grass" in t for t in got)


def test_direct_memory_writes_deduplicate_by_content(tmp_path):
    """A MemoryEntry has no position in a conversation, so identity has to come from the text."""
    s = svc(tmp_path)
    m = MemoryEntry(content=gt.Content(role="user", parts=[gt.Part(text="allergic to penicillin")]))
    run(s.add_memory(app_name="app", user_id="u", memories=[m]))
    run(s.add_memory(app_name="app", user_id="u", memories=[m]))
    got = run(_texts(s, "app", "u", "allergic"))
    assert len([t for t in got if "penicillin" in t]) == 1


def test_deduplication_survives_a_restart(tmp_path):
    """The seen-set is rebuilt from the store, so a restarted process must not re-ingest."""
    session = sess("app", "u", "s1", ["the spare key is under the mat"])
    first = svc(tmp_path)
    run(first.add_session_to_memory(session))
    first.store._save(force=True)

    second = svc(tmp_path)                       # same file, new process would look like this
    run(second.add_session_to_memory(session))
    second.store._save(force=True)
    got = run(_texts(second, "app", "u", "spare key"))
    assert len([t for t in got if "mat" in t]) == 1


def test_users_cannot_see_each_other(tmp_path):
    s = svc(tmp_path)
    run(s.add_session_to_memory(sess("app", "alice", "sa", ["alice keeps her passport in the safe"])))
    run(s.add_session_to_memory(sess("app", "bob", "sb", ["bob keeps his passport in the drawer"])))
    alice = run(_texts(s, "app", "alice", "passport"))
    assert any("safe" in t for t in alice)
    assert not any("drawer" in t for t in alice)


def test_apps_cannot_see_each_other(tmp_path):
    s = svc(tmp_path)
    run(s.add_session_to_memory(sess("app1", "u", "s1", ["the launch code is ALPHA"])))
    run(s.add_session_to_memory(sess("app2", "u", "s2", ["the launch code is BETA"])))
    assert not any("BETA" in t for t in run(_texts(s, "app1", "u", "launch code")))


def test_an_empty_query_returns_nothing(tmp_path):
    s = svc(tmp_path)
    run(s.add_session_to_memory(sess("app", "u", "s", ["the cat is asleep"])))
    assert run(_texts(s, "app", "u", "")) == []


def test_events_without_text_are_skipped(tmp_path):
    s = svc(tmp_path)
    session = Session(app_name="app", user_id="u", id="s",
                      events=[Event(author="user", content=None), ev("   "), ev("a real fact about badgers")])
    run(s.add_session_to_memory(session))
    got = run(_texts(s, "app", "u", "badgers"))
    assert len(got) == 1


def test_erasure_removes_the_value_from_disk(tmp_path):
    s = svc(tmp_path)
    run(s.add_session_to_memory(sess("app", "erase-me", "s", ["my IBAN is SK9911000000002612345678"])))
    s.store._save(force=True)
    s.forget_subject_for("app", "erase-me", request_id="gdpr-1")
    s.store._save(force=True)
    blob = " ".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in tmp_path.rglob("*") if p.is_file())
    assert "2612345678" not in blob
    assert not any("2612345678" in t for t in run(_texts(s, "app", "erase-me", "IBAN")))


def test_from_uri_recovers_the_path_the_user_typed(tmp_path):
    """urlparse splits a filesystem path like a URL; both halves have to rejoin."""
    absolute = tmp_path / "mem.json"
    assert pathlib.Path(InspeximusMemoryService.from_uri(f"inspeximus://{absolute}").store.path) == absolute
    assert pathlib.Path(InspeximusMemoryService.from_uri("inspeximus://mem.json").store.path) \
        == pathlib.Path("mem.json")


def test_register_wires_the_scheme_into_adks_own_registry(tmp_path):
    """This is what makes `adk web --memory_service_uri=inspeximus://...` work."""
    from google.adk.cli.service_registry import get_service_registry
    from inspeximus.integrations.google_adk import register
    register()
    built = get_service_registry().create_memory_service(f"inspeximus://{tmp_path / 'm.json'}")
    assert isinstance(built, InspeximusMemoryService)
    run(built.add_session_to_memory(sess("app", "u", "s", ["the ferry leaves at 6am"])))
    assert any("ferry" in t for t in run(_texts(built, "app", "u", "ferry")))


def test_erasing_one_user_leaves_another_intact(tmp_path):
    s = svc(tmp_path)
    run(s.add_session_to_memory(sess("app", "a", "s1", ["account number 1111 belongs to a"])))
    run(s.add_session_to_memory(sess("app", "b", "s2", ["account number 2222 belongs to b"])))
    s.forget_subject_for("app", "a", request_id="gdpr-2")
    assert any("2222" in t for t in run(_texts(s, "app", "b", "account number")))
