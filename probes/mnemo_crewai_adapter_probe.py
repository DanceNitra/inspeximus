"""mnemo_crewai_adapter_probe.py — MnemoStorage matches CrewAI's Storage protocol + the integrity differentiator.

MnemoStorage is DUCK-TYPED (it does not import crewai), so this probe verifies the contract CrewAI actually
calls — save(value, metadata) / search(query, limit, score_threshold) / reset() — plus the differentiator that
sets mnemo apart from CrewAI's default RAG storage: search() is supersession-filtered, so a corrected (keyed)
fact is never returned back into the crew's context. If crewai is installed, it also asserts MnemoStorage is a
structural substitute for crewai's abstract Storage (same public method names).
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo
from mnemo.integrations.crewai import MnemoStorage


def run():
    ok = {}
    tmp = pathlib.Path(tempfile.mkdtemp())

    st = MnemoStorage(path=str(tmp / "crew.json"))

    # A save() stores, search() returns CrewAI-shaped hits ({"context","metadata","score"})
    st.save("the retry limit is 5 attempts", {"kind": "config"})
    st.save("the project deadline is in July", {})
    res = st.search("what is the retry limit", limit=5)
    ok["A save+search returns relevant hits"] = (
        bool(res) and any("retry limit is 5" in r.get("context", "") for r in res))
    ok["B hit has CrewAI shape (context/metadata/score)"] = (
        bool(res) and all(set(("context", "metadata", "score")) <= set(r.keys()) for r in res))

    # C no relevant memory -> empty list (nothing injected)
    empty = st.search("unrelated question about penguins in space", limit=5)
    ok["C no relevant memory -> empty list"] = (empty == [])

    # D DIFFERENTIATOR: a corrected (keyed) fact is not returned — supersession-filtered search
    st2 = MnemoStorage(path=str(tmp / "sup.json"))
    st2.save("user timezone is UTC", {"key": "user::tz", "object": "UTC"})
    st2.save("user timezone is PST", {"key": "user::tz", "object": "PST"})   # supersedes UTC
    hits = st2.search("user timezone", limit=5)
    joined = " ".join(h.get("context", "") for h in hits)
    ok["D current-truth (PST in, UTC out)"] = ("PST" in joined and "UTC" not in joined)

    # E reset() soft-deletes this storage's memories (search goes empty afterwards)
    st2.reset()
    ok["E reset clears the store"] = (st2.search("user timezone", limit=5) == [])

    # F protocol methods present with the CrewAI names
    ok["F Storage protocol methods present"] = all(
        callable(getattr(st, m, None)) for m in ("save", "search", "reset"))

    # G (only if crewai installed) structurally substitutable for crewai's abstract Storage
    try:
        from crewai.memory.storage.interface import Storage as _CrewStorage
        req = [m for m in dir(_CrewStorage) if not m.startswith("_")]
        ok["G substitutes crewai Storage"] = all(hasattr(st, m) for m in req)
    except Exception:
        pass  # crewai not installed -> duck-typed contract already covered by A-F

    print("=" * 62)
    print("MnemoStorage - CrewAI Storage protocol (save/search/reset) + integrity")
    print("=" * 62)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 62)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(run())
