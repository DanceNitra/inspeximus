"""Run LangGraph's OWN conformance suite against InspeximusSaver.

The store side has no conformance suite -- LangGraph's docs say to test against InMemoryStore, which
is what store_audit.py does. The checkpointer side DOES have one, and the docs say to run it in CI
before shipping. So it runs here before anything is published or listed.
"""
import asyncio, pathlib, tempfile
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
