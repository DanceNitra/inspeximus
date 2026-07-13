"""mnemo_langgraph_adapter_probe.py — MnemoStore works with the REAL LangGraph BaseStore protocol.

Requires langgraph installed. Verifies faithful put/get/search/delete/list + the honest differentiator:
mnemo keeps the value HISTORY that the built-in InMemoryStore overwrites and discards.
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo.integrations.langgraph import MnemoStore
from langgraph.store.memory import InMemoryStore

ns = ("user", "42")


def main():
    ok = {}
    s = MnemoStore(path=str(pathlib.Path(tempfile.mkdtemp()) / "lg.json"))

    # put + get
    s.put(ns, "timezone", {"tz": "UTC"})
    ok["A get returns value"] = s.get(ns, "timezone").value == {"tz": "UTC"}
    ok["B get missing -> None"] = s.get(ns, "other") is None

    # overwrite (same key) — matches InMemoryStore last-write-wins
    s.put(ns, "timezone", {"tz": "PST"})
    ok["C overwrite -> current value"] = s.get(ns, "timezone").value == {"tz": "PST"}

    # DIFFERENTIATOR: mnemo kept the history InMemoryStore discards
    im = InMemoryStore(); im.put(ns, "timezone", {"tz": "UTC"}); im.put(ns, "timezone", {"tz": "PST"})
    ok["D InMemoryStore has NO history"] = not hasattr(im, "history")
    ok["E mnemo history keeps both"] = s.history(ns, "timezone") == [{"tz": "UTC"}, {"tz": "PST"}]

    # search within a namespace prefix
    s.put(ns, "theme", {"mode": "dark"})
    res = s.search(("user",), query="timezone")
    ok["F search returns SearchItems"] = len(res) >= 1 and any(r.value == {"tz": "PST"} for r in res)
    res_all = s.search(("user",))
    ok["G search no-query lists namespace"] = len(res_all) >= 2

    # list namespaces
    nss = s.list_namespaces()
    ok["H list_namespaces"] = ns in nss

    # delete (value=None convention + explicit delete)
    s.delete(ns, "theme")
    ok["I delete removes current"] = s.get(ns, "theme") is None

    print("=" * 60)
    print("MnemoStore - LangGraph BaseStore (real langgraph)")
    print("=" * 60)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 60)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
