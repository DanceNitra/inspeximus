"""Run LangGraph's OWN conformance suite against InspeximusSaver.

The store side has no conformance suite -- LangGraph's docs say to test against InMemoryStore, which
is what store_audit.py does. The checkpointer side DOES have one, and the docs say to run it in CI
before shipping. So it runs here before anything is published or listed.
"""
import asyncio, pathlib, sys, tempfile

# LangGraph's report.py prints U+2705 per capability. On a non-UTF-8 console -- cp1250 is the default
# on this project's Windows dev box -- that raises UnicodeEncodeError from inside the library, so this
# script died before printing a single result. CI runs on ubuntu with UTF-8 and never saw it, which is
# the bad half: the one parity check the docs say to run before shipping was green in CI and
# unrunnable by the maintainer, so nobody could verify it locally. We do not control the library's
# output, so the console is made able to accept it. Same reconfigure the server entrypoint already
# does for the same reason.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from langgraph.checkpoint.conformance import checkpointer_test, validate
from inspeximus.integrations.langgraph import InspeximusSaver

_tmp = pathlib.Path(tempfile.mkdtemp())


@checkpointer_test(name="InspeximusSaver")
async def saver():
    yield InspeximusSaver(path=str(_tmp / "ckpt.jsonl"))


async def main():
    report = await validate(saver)
    report.print_report()
    print()
    print("passed_all_base():", report.passed_all_base())


    if not report.passed_all_base():
        raise SystemExit("checkpointer conformance FAILED on a base capability")


asyncio.run(main())
