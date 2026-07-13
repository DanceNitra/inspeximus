"""mnemo_adk_adapter_probe.py — MnemoMemoryService works with the REAL Google ADK BaseMemoryService.

Requires google-adk. Verifies add_session_to_memory/search_memory round-trip, per-user isolation, the
supersession-filtered current-truth differentiator, and the per-user erasure bonus.
"""
import sys, pathlib, asyncio, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo, new_receipt_keypair
from mnemo.integrations.google_adk import MnemoMemoryService
from google.adk.sessions import Session
from google.adk.events import Event
from google.genai import types as gt


def _sess(app, user, sid, texts, author="user"):
    events = [Event(author=author, content=gt.Content(role="user", parts=[gt.Part(text=t)])) for t in texts]
    return Session(id=sid, app_name=app, user_id=user, events=events)


async def run():
    ok = {}
    tmp = pathlib.Path(tempfile.mkdtemp())
    svc = MnemoMemoryService(path=str(tmp / "m.json"), k=10)

    await svc.add_session_to_memory(_sess("app", "alice", "s1",
        ["the retry limit is 5 attempts", "the project deadline is in July"]))
    await svc.add_session_to_memory(_sess("app", "bob", "s2", ["bob likes dark mode"]))

    # A search returns relevant MemoryEntry for the right user
    r = await svc.search_memory(app_name="app", user_id="alice", query="what is the retry limit")
    texts = [" ".join(p.text for p in mm.content.parts) for mm in r.memories]
    ok["A search returns MemoryEntry"] = any("retry limit is 5" in t for t in texts)

    # B per-user isolation: alice's query never returns bob's memory
    ok["B user isolation"] = not any("dark mode" in t for t in texts)
    rb = await svc.search_memory(app_name="app", user_id="bob", query="dark mode")
    ok["B user isolation"] = ok["B user isolation"] and any(
        "dark mode" in " ".join(p.text for p in mm.content.parts) for mm in rb.memories)

    # C differentiator: a corrected (keyed) fact is not returned — supersession-filtered
    store = Mnemo(path=str(tmp / "sup.json"))
    subj = "adk::app::carol"
    store.remember("carol timezone is UTC", key="carol::tz", object="UTC", source={"doc": subj},
                   meta={"adk_app": "app", "adk_user": "carol", "adk_role": "user"})
    store.remember("carol timezone is PST", key="carol::tz", object="PST", source={"doc": subj},
                   meta={"adk_app": "app", "adk_user": "carol", "adk_role": "user"})
    svc2 = MnemoMemoryService(store=store, k=10)
    rc = await svc2.search_memory(app_name="app", user_id="carol", query="carol timezone")
    ctext = " ".join(" ".join(p.text for p in mm.content.parts) for mm in rc.memories)
    ok["C current-truth (PST in, UTC out)"] = ("PST" in ctext and "UTC" not in ctext)

    # D erasure bonus: forget a user + signed tombstone, audit stays intact
    sk, pk = new_receipt_keypair()
    gstore = Mnemo(path=str(tmp / "gov.json"), receipts=True, receipt_key=sk, receipt_pubkey=pk)
    gsvc = MnemoMemoryService(store=gstore)
    await gsvc.add_session_to_memory(_sess("app", "dave", "s3", ["dave secret note"]))
    res = gsvc.forget_subject_for("app", "dave", request_id="dsar-1")
    verify_ok, _ = gstore.verify_writes(expected_pubkey=pk)
    rd = await gsvc.search_memory(app_name="app", user_id="dave", query="secret note")
    ok["D erasure + accounted-for audit"] = (res["erased"] >= 1 and verify_ok and rd.memories == [])

    print("=" * 60)
    print("MnemoMemoryService - Google ADK BaseMemoryService (real google-adk)")
    print("=" * 60)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 60)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
