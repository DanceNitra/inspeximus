"""Does InspeximusSession behave like an OpenAI Agents SDK session? Checked against their own SQLiteSession.

The SDK defines a `Session` protocol (`add_items` / `get_items` / `pop_item` / `clear_session`) and ships
`SQLiteSession` as the reference. "Drop-in session" is only a real claim if a caller cannot tell the two
apart, so every operation below runs against both and the observable results must match.

    python session_audit.py                    # working tree
    SESSION_FALSIFY=1 python session_audit.py  # breaks ours on purpose; the checks MUST fail

Requires: pip install openai-agents
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

from agents.memory.sqlite_session import SQLiteSession   # noqa: E402

from inspeximus.integrations.openai_agents import InspeximusSession   # noqa: E402


def msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


TURNS = [msg("user", "my dentist is Dr. Kovac"), msg("assistant", "noted"),
         msg("user", "actually my dentist is Dr. Bartos"), msg("assistant", "updated")]


# Each scenario returns a dict of observable properties, compared between the reference and ours.
async def sc_roundtrip(s):
    await s.add_items(TURNS[:2])
    got = await s.get_items()
    return {"stores both turns in order": [i["content"] for i in got] == [t["content"] for t in TURNS[:2]]}


async def sc_limit_returns_the_latest(s):
    """The SDK's own docstring: limit returns the LATEST N items, in chronological order."""
    await s.add_items(TURNS)
    got = await s.get_items(limit=2)
    return {"limit=2 returns the last two, oldest first":
            [i["content"] for i in got] == [TURNS[2]["content"], TURNS[3]["content"]],
            "limit=0 returns nothing": await s.get_items(limit=0) == [],
            "limit larger than the history returns everything": len(await s.get_items(limit=99)) == 4}


async def sc_pop_is_lifo(s):
    await s.add_items(TURNS)
    popped = await s.pop_item()
    rest = await s.get_items()
    return {"pop returns the newest item": popped["content"] == TURNS[3]["content"],
            "pop removes exactly one": len(rest) == 3,
            "pop on an empty session returns None": await _pop_until_empty(s) is None}


async def _pop_until_empty(s):
    for _ in range(20):
        if await s.pop_item() is None:
            return None
    return "never emptied"


async def sc_clear(s):
    await s.add_items(TURNS)
    await s.clear_session()
    return {"clear empties the session": await s.get_items() == [],
            "clear on an empty session is not an error": (await s.clear_session()) is None}


async def sc_empty_session(s):
    return {"a fresh session is empty": await s.get_items() == [],
            "pop on a fresh session returns None": await s.pop_item() is None}


async def sc_adding_nothing(s):
    await s.add_items([])
    return {"adding an empty list is a no-op": await s.get_items() == []}


async def sc_isolation(s, other):
    """Two session ids must not see each other's turns."""
    await s.add_items([msg("user", "session one secret ALPHA")])
    await other.add_items([msg("user", "session two secret BETA")])
    mine = [i["content"] for i in await s.get_items()]
    return {"sees its own": any("ALPHA" in c for c in mine),
            "cannot see the other session": not any("BETA" in c for c in mine)}


SCENARIOS = [
    ("round-trip", sc_roundtrip, False),
    ("limit returns the latest", sc_limit_returns_the_latest, False),
    ("pop is last-in-first-out", sc_pop_is_lifo, False),
    ("clear", sc_clear, False),
    ("a fresh session", sc_empty_session, False),
    ("adding an empty list", sc_adding_nothing, False),
    ("session isolation", sc_isolation, True),
]


def _build(kind, tmp, sid):
    if kind == "ref":
        return SQLiteSession(sid, str(tmp / "ref.db"))
    return InspeximusSession(session_id=sid, path=str(tmp / "ours.json"))


async def run_one(fn, needs_second, kind, tmp):
    a = _build(kind, tmp, "s1")
    if kind == "ours" and os.environ.get("SESSION_FALSIFY") == "1":
        async def _swallow(items):
            return None
        a.add_items = _swallow            # writes go nowhere; every check must fail
    try:
        if needs_second:
            return await fn(a, _build(kind, tmp, "s2"))
        return await fn(a)
    except Exception as e:
        return {"RAISED": f"{type(e).__name__}: {e}"}


async def main_async(repeats):
    print("=" * 92)
    print(f"InspeximusSession vs the SDK's own SQLiteSession -- {len(SCENARIOS)} scenarios x {repeats} repeats")
    if os.environ.get("SESSION_FALSIFY") == "1":
        print("FALSIFY MODE: writes are swallowed; the checks MUST fail")
    print("=" * 92)

    fails = 0
    for name, fn, needs_second in SCENARIOS:
        print(f"\n--- {name}")
        seen = []
        for run in range(repeats):
            tmp = pathlib.Path(tempfile.mkdtemp(prefix="sess_"))
            r_ref = await run_one(fn, needs_second, "ref", tmp)
            r_ours = await run_one(fn, needs_second, "ours", tmp)
            shutil.rmtree(tmp, ignore_errors=True)
            seen.append(tuple(sorted((k, str(v)) for k, v in r_ours.items())))
            if run == 0:
                for k in sorted(set(r_ref) | set(r_ours)):
                    a, b = r_ref.get(k), r_ours.get(k)
                    worse = a != b
                    fails += bool(worse)
                    print(f"  [{'MISMATCH' if worse else 'ok  '}] {k:48} ref={str(a):6} ours={str(b)}")
        if len(set(seen)) != 1:
            fails += 1
            print(f"  [FAIL] results not identical across {repeats} runs")
        else:
            print(f"  [PASS] identical results across {repeats} runs")

    print("\n" + "=" * 92)
    print("InspeximusSession matches the reference session" if not fails
          else f"NOT a drop-in -- {fails} mismatches")
    print("=" * 92)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    return asyncio.run(main_async(ap.parse_args().repeats))


if __name__ == "__main__":
    raise SystemExit(main())
