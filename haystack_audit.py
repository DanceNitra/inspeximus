"""Does InspeximusDocumentStore behave like a Haystack DocumentStore? Checked against their own.

"Drop-in replacement for InMemoryDocumentStore" is only meaningful if a pipeline cannot tell the two
apart, so every scenario below runs against both `haystack.document_stores.in_memory.InMemoryDocumentStore`
and ours, and the observable results must match. Where they differ by design -- ours persists, and its
delete removes the value from disk -- is stated and checked separately.

    python haystack_audit.py                  # working tree
    HAYSTACK_FALSIFY=1 python haystack_audit.py  # breaks ours on purpose; the checks MUST fail

Requires: pip install haystack-ai
"""
import argparse
import os
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from haystack.dataclasses.document import Document                      # noqa: E402
from haystack.document_stores.in_memory import InMemoryDocumentStore    # noqa: E402
from haystack.document_stores.types import DuplicatePolicy              # noqa: E402

from inspeximus.integrations.haystack import InspeximusDocumentStore    # noqa: E402


def docs():
    return [Document(id="1", content="the invoice is due in March", meta={"kind": "invoice", "year": 2026}),
            Document(id="2", content="the manager is Rachel Tseng", meta={"kind": "person", "year": 2025}),
            Document(id="3", content="the invoice number is 9981", meta={"kind": "invoice", "year": 2026})]


def _ids(result):
    return sorted(d.id for d in result)


def sc_write_and_count(s):
    n = s.write_documents(docs())
    return {"write returns the number written": n == 3,
            "count reflects the writes": s.count_documents() == 3}


def sc_roundtrip(s):
    s.write_documents([docs()[0]])
    got = s.filter_documents()[0]
    d = docs()[0]
    return {"content preserved": got.content == d.content,
            "meta preserved": got.meta == d.meta,
            "id preserved": got.id == d.id}


def sc_filter(s):
    s.write_documents(docs())
    invoices = s.filter_documents({"field": "meta.kind", "operator": "==", "value": "invoice"})
    y2026 = s.filter_documents({"field": "meta.year", "operator": ">=", "value": 2026})
    none = s.filter_documents({"field": "meta.kind", "operator": "==", "value": "nope"})
    return {"equality filter": _ids(invoices) == ["1", "3"],
            "comparison filter": _ids(y2026) == ["1", "3"],
            "filter with no match is empty": none == []}


def sc_policy_skip(s):
    s.write_documents([Document(id="1", content="original")])
    n = s.write_documents([Document(id="1", content="replacement")], policy=DuplicatePolicy.SKIP)
    kept = s.filter_documents()[0].content
    return {"SKIP writes nothing": n == 0,
            "SKIP keeps the original": kept == "original",
            "SKIP leaves count at one": s.count_documents() == 1}


def sc_policy_overwrite(s):
    s.write_documents([Document(id="1", content="original")])
    n = s.write_documents([Document(id="1", content="replacement")], policy=DuplicatePolicy.OVERWRITE)
    kept = s.filter_documents()[0].content
    return {"OVERWRITE writes one": n == 1,
            "OVERWRITE replaces content": kept == "replacement",
            "OVERWRITE leaves count at one": s.count_documents() == 1}


def sc_policy_fail(s):
    s.write_documents([Document(id="1", content="original")])
    try:
        s.write_documents([Document(id="1", content="x")], policy=DuplicatePolicy.FAIL)
        raised = False
    except Exception as e:
        raised = type(e).__name__ == "DuplicateDocumentError"
    return {"FAIL raises DuplicateDocumentError on a repeated id": raised,
            "FAIL left the original in place": s.filter_documents()[0].content == "original"}


def sc_delete(s):
    s.write_documents(docs())
    s.delete_documents(["2"])
    remaining = _ids(s.filter_documents())
    s.delete_documents(["does-not-exist"])
    return {"delete removes the document": remaining == ["1", "3"],
            "deleting an unknown id is a no-op": s.count_documents() == 2}


SCENARIOS = [
    ("write and count", sc_write_and_count),
    ("a document round-trips unchanged", sc_roundtrip),
    ("filters match Haystack semantics", sc_filter),
    ("DuplicatePolicy.SKIP", sc_policy_skip),
    ("DuplicatePolicy.OVERWRITE", sc_policy_overwrite),
    ("DuplicatePolicy.FAIL", sc_policy_fail),
    ("delete, including an unknown id", sc_delete),
]


def _build(kind, tmp):
    if kind == "ref":
        return InMemoryDocumentStore()
    s = InspeximusDocumentStore(path=str(tmp / "docs.json"))
    if os.environ.get("HAYSTACK_FALSIFY") == "1":
        s.write_documents = lambda *a, **k: 0     # swallow writes; every check must fail
    return s


def report_by_design():
    tmp = pathlib.Path(tempfile.mkdtemp())
    print()
    print("--- differences by design (ours only)")
    rows = []
    s = InspeximusDocumentStore(path=str(tmp / "p.json"))
    s.write_documents([Document(id="1", content="the spare key is under the mat")])
    reopened = InspeximusDocumentStore(path=str(tmp / "p.json"))
    rows.append(("survives a reopen", reopened.count_documents() == 1))

    s2 = InspeximusDocumentStore(path=str(tmp / "e.json"))
    s2.write_documents([Document(id="9", content="IBAN SK9911000000002612345678")])
    s2.delete_documents(["9"])
    s2.store._save(force=True)
    blob = " ".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in tmp.rglob("*") if p.is_file())
    rows.append(("deleted value is gone from disk", "2612345678" not in blob))

    for label, ok in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    shutil.rmtree(tmp, ignore_errors=True)
    return all(ok for _, ok in rows)


def run_one(fn, kind):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="hs_"))
    try:
        return fn(_build(kind, tmp))
    except Exception as e:
        return {"RAISED": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()
    import haystack
    print("=" * 92)
    print(f"InspeximusDocumentStore vs haystack-ai {haystack.__version__} InMemoryDocumentStore "
          f"-- {len(SCENARIOS)} scenarios x {a.repeats} repeats")
    if os.environ.get("HAYSTACK_FALSIFY") == "1":
        print("FALSIFY MODE: writes are swallowed; the checks MUST fail")
    print("=" * 92)

    fails = 0
    for name, fn in SCENARIOS:
        print(f"\n--- {name}")
        seen = []
        for run in range(a.repeats):
            r_ref = run_one(fn, "ref")
            r_ours = run_one(fn, "ours")
            seen.append(tuple(sorted((k, str(v)) for k, v in r_ours.items())))
            if run == 0:
                for k in sorted(set(r_ref) | set(r_ours)):
                    ref, ours = r_ref.get(k), r_ours.get(k)
                    bad = ref != ours
                    fails += bool(bad)
                    print(f"  [{'MISMATCH' if bad else 'ok  '}] {k:48} ref={str(ref):6} ours={str(ours)}")
        if len(set(seen)) != 1:
            fails += 1
            print(f"  [FAIL] not identical across {a.repeats} runs")
        else:
            print(f"  [PASS] identical across {a.repeats} runs")

    if os.environ.get("HAYSTACK_FALSIFY") != "1":
        fails += not report_by_design()

    print("\n" + "=" * 92)
    print("InspeximusDocumentStore IS a drop-in Haystack DocumentStore" if not fails
          else f"NOT drop-in -- {fails} failing checks")
    print("=" * 92)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    __import__("_key_home").isolate("haystack_audit")    # heads and keys out of the real key home
    raise SystemExit(main())
