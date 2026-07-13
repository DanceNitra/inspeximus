"""extractor_adapter_wireup_probe.py — plugging extractor= into an adapter makes free-text writes current-truth.

Requires autogen-core (the clearest current-truth adapter). With extractor= plugged, two conflicting free-text
messages auto-key and supersede, so query/update_context returns only the current value — no manual keying.
"""
import sys, pathlib, asyncio, tempfile, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo.integrations.autogen import MnemoMemory
from autogen_core.memory import MemoryContent, MemoryMimeType

def extract(t):
    m = re.match(r"(.+?) is (\w+)", t.strip().rstrip("."))
    return (f"fact::{m[1].strip().lower()}", m[2].lower()) if m else None

async def run():
    ok = {}
    tmp = pathlib.Path(tempfile.mkdtemp())

    # WITHOUT extractor: free-text add() does not supersede -> both values recallable (stale leaks)
    m0 = MnemoMemory(path=str(tmp / "a.json"), k=5)
    await m0.add(MemoryContent(content="server timezone is UTC", mime_type=MemoryMimeType.TEXT))
    await m0.add(MemoryContent(content="server timezone is PST", mime_type=MemoryMimeType.TEXT))
    t0 = [c.content for c in (await m0.query("server timezone")).results]
    ok["A no extractor -> stale value still present"] = any("UTC" in x for x in t0)

    # WITH extractor= wired: same free-text adds auto-key + supersede -> only current-truth returned
    m1 = MnemoMemory(path=str(tmp / "b.json"), k=5, extractor=extract)
    await m1.add(MemoryContent(content="server timezone is UTC", mime_type=MemoryMimeType.TEXT))
    await m1.add(MemoryContent(content="server timezone is PST", mime_type=MemoryMimeType.TEXT))
    t1 = [c.content for c in (await m1.query("server timezone")).results]
    ok["B extractor= wired -> current-truth only (PST, no UTC)"] = (any("PST" in x for x in t1)
                                                                    and not any("UTC" in x for x in t1))

    print("=" * 62)
    print("extractor= wired into an adapter -> free-text current-truth")
    print("=" * 62)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 62)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
