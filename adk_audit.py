"""Does InspeximusMemoryService behave like an ADK memory service? Checked against ADK's own.

ADK ships no conformance suite for `BaseMemoryService` (LangGraph ships one for checkpointers; Google
does not). So "drop-in replacement for InMemoryMemoryService" is a claim with nothing behind it unless
we build the check ourselves. Every scenario below runs TWICE -- once against
`google.adk.memory.InMemoryMemoryService` (the reference) and once against ours -- and the properties
a caller depends on must hold for both.

Where the two MUST differ is stated explicitly rather than hidden: the reference is keyword matching
and keeps every event forever; ours ranks by relevance and can hide a corrected value. Those are
recorded as differences by design and are checked separately, not smuggled into the parity score.

    python adk_audit.py                 # working tree
    ADK_FALSIFY=1 python adk_audit.py   # breaks ours on purpose; the checks MUST fail

Requires the `adk` extra: pip install google-adk
"""
import argparse
import asyncio
import os
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from google.adk.events.event import Event                      # noqa: E402
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService   # noqa: E402
from google.adk.sessions.session import Session                # noqa: E402
from google.genai import types as gt                           # noqa: E402

from inspeximus.integrations.google_adk import InspeximusMemoryService   # noqa: E402


def ev(author: str, text: str, eid: str | None = None) -> Event:
    e = Event(author=author, content=gt.Content(role=author, parts=[gt.Part(text=text)]))
    if eid:
        e.id = eid
    return e


def session(app: str, user: str, sid: str, texts) -> Session:
    return Session(app_name=app, user_id=user, id=sid,
                   events=[ev("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(texts)])


async def texts_of(svc, app, user, query):
    r = await svc.search_memory(app_name=app, user_id=user, query=query)
    return [" ".join(p.text for p in m.content.parts if p.text) for m in r.memories]


# ---------------------------------------------------------------- scenarios
# Each returns (name, coroutine(svc) -> dict of observable properties).
# Properties are compared between the reference and ours; every one is something a caller relies on.

async def sc_ingest_and_find(svc):
    await svc.add_session_to_memory(session("app", "u1", "s1", [
        "my dentist is Dr. Kovac in Nitra", "noted", "I drive a blue Skoda"]))
    got = await texts_of(svc, "app", "u1", "dentist")
    return {"finds the dentist fact": any("Kovac" in t for t in got),
            "returns something": len(got) > 0}


async def sc_user_isolation(svc):
    await svc.add_session_to_memory(session("app", "alice", "sa", ["alice keeps her passport in the safe"]))
    await svc.add_session_to_memory(session("app", "bob", "sb", ["bob keeps his passport in the drawer"]))
    alice = await texts_of(svc, "app", "alice", "passport")
    bob = await texts_of(svc, "app", "bob", "passport")
    return {"alice sees her own": any("safe" in t for t in alice),
            "alice cannot see bob": not any("drawer" in t for t in alice),
            "bob cannot see alice": not any("safe" in t for t in bob)}


async def sc_app_isolation(svc):
    await svc.add_session_to_memory(session("app1", "u", "s1", ["the launch code is ALPHA"]))
    await svc.add_session_to_memory(session("app2", "u", "s2", ["the launch code is BETA"]))
    a1 = await texts_of(svc, "app1", "u", "launch code")
    return {"app1 sees its own": any("ALPHA" in t for t in a1),
            "app1 cannot see app2": not any("BETA" in t for t in a1)}


async def sc_empty_and_miss(svc):
    await svc.add_session_to_memory(session("app", "u", "s", ["the cat is asleep"]))
    empty = await texts_of(svc, "app", "u", "")
    miss = await texts_of(svc, "app", "u", "zzzqqq")
    unknown = await texts_of(svc, "app", "nobody", "cat")
    return {"empty query returns nothing": empty == [],
            "unrelated query returns no cat": not any("cat" in t for t in miss),
            "unknown user returns nothing": unknown == []}


async def sc_multiple_sessions(svc):
    await svc.add_session_to_memory(session("app", "u", "s1", ["I am allergic to penicillin"]))
    await svc.add_session_to_memory(session("app", "u", "s2", ["I moved to Kosice"]))
    got = await texts_of(svc, "app", "u", "allergic")
    return {"finds a fact from an earlier session": any("penicillin" in t for t in got)}


async def sc_reingest_same_session(svc):
    """"A session may be added multiple times during its lifetime" -- BaseMemoryService docstring.

    So re-adding a session must not multiply its events. The reference keys by session id and replaces.
    """
    s = session("app", "u", "s1", ["the meeting is at noon"])
    await svc.add_session_to_memory(s)
    await svc.add_session_to_memory(s)
    got = await texts_of(svc, "app", "u", "meeting")
    return {"re-adding a session does not duplicate it": len([t for t in got if "noon" in t]) == 1}


async def sc_event_deltas(svc):
    """The optional delta path. Defaults raise NotImplementedError; the reference implements it."""
    try:
        await svc.add_events_to_memory(app_name="app", user_id="u",
                                       events=[ev("user", "the wifi password is hunter2", "e1")],
                                       session_id="s1")
        await svc.add_events_to_memory(app_name="app", user_id="u",
                                       events=[ev("user", "the wifi password is hunter2", "e1")],
                                       session_id="s1")
        got = await texts_of(svc, "app", "u", "wifi password")
        return {"delta ingest supported": True,
                "delta ingest finds the fact": any("hunter2" in t for t in got),
                "repeating the same event id does not duplicate": len([t for t in got if "hunter2" in t]) == 1}
    except NotImplementedError:
        return {"delta ingest supported": False,
                "delta ingest finds the fact": False,
                "repeating the same event id does not duplicate": False}


async def sc_crowded_store(svc):
    """The silent-cap check: many users' memories ranked above the one we asked for.

    An implementation that fetches a fixed window and then filters by user returns an empty result once
    enough other users outrank the target. The failure is invisible -- it looks like "no memories".
    """
    for i in range(60):
        await svc.add_session_to_memory(session("app", f"noise{i}", f"n{i}", [
            f"user {i} says the quarterly revenue target is important"]))
    await svc.add_session_to_memory(session("app", "target", "st", [
        "the quarterly revenue target is 4.2 million"]))
    got = await texts_of(svc, "app", "target", "quarterly revenue target")
    return {"finds its own memory among 60 other users": any("4.2 million" in t for t in got)}


SCENARIOS = [
    ("ingest a session and retrieve from it", sc_ingest_and_find),
    ("user isolation", sc_user_isolation),
    ("app isolation", sc_app_isolation),
    ("empty query, missed query, unknown user", sc_empty_and_miss),
    ("facts span multiple sessions", sc_multiple_sessions),
    ("re-adding a session does not duplicate", sc_reingest_same_session),
    ("incremental event deltas", sc_event_deltas),
    ("crowded store: 60 other users", sc_crowded_store),
]


# ---------------------------------------------------------------- differences by design
async def report_by_design():
    """What ours does that the reference cannot. Checked, not asserted."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    print()
    print("--- differences by design (ours only; the reference cannot do these)")

    ours = InspeximusMemoryService(path=str(tmp / "a.json"))
    ref = InMemoryMemoryService()
    rows = []

    # 1. persistence across process restart
    await ours.add_session_to_memory(session("app", "u", "s", ["the spare key is under the mat"]))
    ours.store._save(force=True)
    reopened = InspeximusMemoryService(path=str(tmp / "a.json"))
    got = await texts_of(reopened, "app", "u", "spare key")
    rows.append(("survives a restart", any("mat" in t for t in got)))

    # 2. right to erasure, and the value must leave the bytes on disk
    await ours.add_session_to_memory(session("app", "erase-me", "s2", ["my IBAN is SK9911000000002612345678"]))
    ours.store._save(force=True)
    ours.forget_subject_for("app", "erase-me", request_id="gdpr-1")
    ours.store._save(force=True)
    blob = " ".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in tmp.rglob("*") if p.is_file())
    rows.append(("erased user's value is gone from disk", "2612345678" not in blob))
    left = await texts_of(ours, "app", "erase-me", "IBAN")
    rows.append(("erased user's memory no longer retrievable", not any("2612345678" in t for t in left)))

    # 3. the reference keeps an erased user forever (this is the point of the feature)
    await ref.add_session_to_memory(session("app", "erase-me", "s2", ["my IBAN is SK9911000000002612345678"]))
    ref_left = await texts_of(ref, "app", "erase-me", "IBAN")
    rows.append(("reference has no erasure at all (expected True)", any("2612345678" in t for t in ref_left)))

    for label, ok in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    shutil.rmtree(tmp, ignore_errors=True)
    return all(ok for _, ok in rows)


# ---------------------------------------------------------------- driver
async def run_one(name, fn, run_idx):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"adk_{run_idx}_"))
    ref = InMemoryMemoryService()
    ours = InspeximusMemoryService(path=str(tmp / "m.json"))
    if os.environ.get("ADK_FALSIFY") == "1":
        async def _swallow(*a, **k):
            return None
        ours.add_session_to_memory = _swallow      # writes go nowhere; every check must fail
    try:
        r_ref = await fn(ref)
    except Exception as e:
        r_ref = {"RAISED": f"{type(e).__name__}: {e}"}
    try:
        r_ours = await fn(ours)
    except Exception as e:
        r_ours = {"RAISED": f"{type(e).__name__}: {e}"}
    shutil.rmtree(tmp, ignore_errors=True)
    return r_ref, r_ours


async def main_async(repeats):
    import google.adk
    print("=" * 96)
    print(f"InspeximusMemoryService vs google-adk {google.adk.__version__} InMemoryMemoryService "
          f"-- {len(SCENARIOS)} scenarios x {repeats} repeats")
    if os.environ.get("ADK_FALSIFY") == "1":
        print("FALSIFY MODE: ingestion is swallowed; the checks MUST fail")
    print("=" * 96)

    fails = 0
    for name, fn in SCENARIOS:
        print(f"\n--- {name}")
        seen = []
        for run in range(repeats):
            r_ref, r_ours = await run_one(name, fn, run)
            seen.append(tuple(sorted((k, str(v)) for k, v in r_ours.items())))
            if run == 0:
                keys = sorted(set(r_ref) | set(r_ours))
                for k in keys:
                    a, b = r_ref.get(k), r_ours.get(k)
                    # ours must not be worse than the reference on any property a caller relies on
                    worse = (a is True and b is not True) or (a is False and b is True and k.endswith("cannot see"))
                    fails += bool(worse)
                    mark = "FAIL" if worse else ("ok  " if b is True else "----")
                    print(f"  [{mark}] {k:52} ref={str(a):6} ours={str(b)}")
        if len(set(seen)) != 1:
            fails += 1
            print(f"  [FAIL] results not identical across {repeats} runs")
        else:
            print(f"  [PASS] identical results across {repeats} runs")

    if os.environ.get("ADK_FALSIFY") != "1":
        fails += not await report_by_design()

    print("\n" + "=" * 96)
    print("InspeximusMemoryService IS a drop-in ADK memory service" if not fails
          else f"NOT drop-in -- {fails} failing checks")
    print("=" * 96)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()
    return asyncio.run(main_async(a.repeats))


if __name__ == "__main__":
    raise SystemExit(main())
