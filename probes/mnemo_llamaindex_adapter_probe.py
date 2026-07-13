"""mnemo_llamaindex_adapter_probe.py — MnemoMemoryBlock works with the REAL LlamaIndex BaseMemoryBlock.

Requires llama-index-core. Verifies the block protocol (_aput/_aget round-trip) + the differentiator:
recall is supersession-filtered, so a corrected (keyed) fact is not injected back into the prompt.
"""
import sys, pathlib, asyncio, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo
from mnemo.integrations.llamaindex import MnemoMemoryBlock
from llama_index.core.base.llms.types import ChatMessage


async def run():
    ok = {}
    tmp = pathlib.Path(tempfile.mkdtemp())

    blk = MnemoMemoryBlock(name="mnemo", path=str(tmp / "m.json"), k=5)

    # A _aput stores, _aget retrieves relevant content
    await blk._aput([ChatMessage(role="user", content="the retry limit is 5 attempts"),
                     ChatMessage(role="user", content="the project deadline is in July")])
    got = await blk._aget([ChatMessage(role="user", content="what is the retry limit")])
    ok["A put+get injects relevant memory"] = ("retry limit is 5" in got)

    # B empty query / no match -> empty string (nothing injected)
    empty = await blk._aget([ChatMessage(role="user", content="unrelated question about penguins in space")])
    ok["B no relevant memory -> empty"] = (empty == "")

    # C DIFFERENTIATOR: a corrected (keyed) fact is not re-injected — supersession-filtered recall
    store = Mnemo(path=str(tmp / "sup.json"))
    store.remember("user timezone is UTC", key="user::tz", object="UTC")
    store.remember("user timezone is PST", key="user::tz", object="PST")   # supersedes UTC
    blk2 = MnemoMemoryBlock(name="mnemo2", store=store, k=5)
    inj = await blk2._aget([ChatMessage(role="user", content="user timezone")])
    ok["C current-truth (PST in, UTC out)"] = ("PST" in inj and "UTC" not in inj)

    # D block is a real BaseMemoryBlock (name/priority fields present)
    ok["D is a BaseMemoryBlock"] = (blk.name == "mnemo" and hasattr(blk, "priority"))

    print("=" * 62)
    print("MnemoMemoryBlock - LlamaIndex BaseMemoryBlock (real llama-index-core)")
    print("=" * 62)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 62)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
