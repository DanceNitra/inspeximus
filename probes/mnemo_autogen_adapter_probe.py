"""mnemo_autogen_adapter_probe.py — MnemoMemory works with the REAL autogen-core Memory protocol.

Requires autogen-core installed (a distribution adapter must be verified against the real SDK, not a shim).
Verifies the faithful protocol + the REAL differentiator: update_context/query inject only CURRENT-TRUTH
facts (a superseded value is hidden), which a plain list-memory cannot do.
"""
import sys, pathlib, asyncio, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo.integrations.autogen import MnemoMemory
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_core.models import UserMessage, SystemMessage
from autogen_core.model_context import UnboundedChatCompletionContext


async def run():
    ok = {}
    path = str(pathlib.Path(tempfile.mkdtemp()) / "m.json")
    m = MnemoMemory(path=path, k=5)

    # add a keyed fact, then supersede it; plus an unrelated fact
    await m.add(MemoryContent(content="user timezone is UTC", mime_type=MemoryMimeType.TEXT,
                              metadata={"key": "user::timezone", "object": "UTC"}))
    await m.add(MemoryContent(content="user prefers dark mode", mime_type=MemoryMimeType.TEXT,
                              metadata={"key": "user::theme", "object": "dark"}))
    await m.add(MemoryContent(content="user timezone is PST", mime_type=MemoryMimeType.TEXT,
                              metadata={"key": "user::timezone", "object": "PST"}))   # supersedes UTC

    qr = await m.query("what is the user timezone")
    texts = [c.content for c in qr.results]
    ok["A query returns MemoryContent"] = all(isinstance(c, MemoryContent) for c in qr.results)
    ok["B current-truth (PST present)"] = any("PST" in t for t in texts)
    ok["C superseded hidden (no UTC)"] = not any("UTC" in t for t in texts)

    # update_context injects a SystemMessage with current-truth into a REAL model context
    ctx = UnboundedChatCompletionContext()
    await ctx.add_message(UserMessage(content="what timezone am I in?", source="user"))
    res = await m.update_context(ctx)
    msgs = await ctx.get_messages()
    sys_msgs = [x for x in msgs if isinstance(x, SystemMessage)]
    injected = " ".join(x.content for x in sys_msgs)
    ok["D update_context added a SystemMessage"] = len(sys_msgs) == 1
    ok["E injected current-truth (PST, not UTC)"] = ("PST" in injected and "UTC" not in injected)
    ok["F returns UpdateContextResult"] = hasattr(res, "memories") and hasattr(res.memories, "results")

    # clear empties the store
    await m.clear()
    ok["G clear empties"] = (await m.query("timezone")).results == []

    print("=" * 64)
    print("MnemoMemory - AutoGen Memory protocol (real autogen-core)")
    print("=" * 64)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 64)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
