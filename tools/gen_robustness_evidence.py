"""Write inspeximus/robustness_evidence.json: the Art. 15 evidence rows compliance_report() carries.

Each row names a probe in probes/, its receipt, the receipt's sha256 as it stands in this tree, the
metric read out of it and the date the receipt was last committed. The numbers are READ from the
receipts here, never typed: three numbers written by hand were all three wrong once
(memory: three-numbers-written-by-hand-all-three-wrong). `--check` exits non-zero when the packaged
file no longer matches what this tool would write, which is the CI leg.

    python tools/gen_robustness_evidence.py            # write
    python tools/gen_robustness_evidence.py --check    # verify only
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inspeximus", "robustness_evidence.json")


def _sha(path):
    """sha256 over the receipt with line endings normalised to LF. A checkout with autocrlf holds
    the same file as CRLF, and hashing the raw bytes made every row read STALE on the other
    platform: measured 2026-09-22, three of three rows STALE on Linux CI, packaged on Windows."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def _committed(path):
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path], cwd=ROOT,
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or None
    except Exception:                                            # noqa: BLE001
        return None


def _echo(receipt):
    row = next(r for r in receipt["rows"] if r["policy"] == "(default)")
    return {"metric": "echo_blocked under the default policy", "value": row["echo_blocked"],
            "summary": "a restated stale value is retired on arrival (echo_blocked %.2f); the safe, context and "
                       "trusting policies are the panel's other rows" % row["echo_blocked"]}


def _poison(receipt):
    raw = [r["raw_hijack"] for r in receipt]
    gated = [r["influence_hijack"] for r in receipt]
    return {"metric": "influence_hijack across %d dense retrievers (raw_hijack without the gate)" % len(receipt),
            "value": max(gated),
            "summary": "AgentPoison-style planted memories hijack %.3f to %.3f of queries at the retriever and %.3f "
                       "once the influence gate decides what may DRIVE a response" % (min(raw), max(raw), max(gated))}


def _split(receipt):
    return {"metric": "fork detected on a forked co-signed pair; not on the honest pair",
            "value": 1.0 if receipt["verdict"] == "DETECTED" else 0.0,
            "summary": "two histories served to two readers are proven from one witness's two signatures "
                       "(fork %s), and an append-only pair is not flagged (control fork %s)"
                       % (receipt["forked_pair"]["fork"], receipt["honest_pair_control"]["fork"])}


ROWS = [
    ("echo", "probes/echo_policy_panel.py", "probes/echo_policy_panel_result.json", _echo,
     "resilience to a corrected fact being re-asserted"),
    ("poison", "probes/agentpoison_influence_gate.py", "probes/agentpoison_influence_gate_result.json", _poison,
     "resilience to memory poisoning"),
    ("split_view", "probes/a_split_view_is_detected_and_an_honest_pair_is_not.py",
     "probes/a_split_view_is_detected_and_an_honest_pair_is_not.result.json", _split,
     "detection of operator-side tampering (a split view)"),
]


def build():
    rows = []
    for rid, probe, receipt, extract, prop in ROWS:
        rpath = os.path.join(ROOT, receipt)
        with open(rpath, encoding="utf-8") as fh:
            data = json.load(fh)
        row = {"id": rid, "article": "AI Act Art. 15", "property": prop, "probe": probe, "receipt": receipt,
               "receipt_sha256": _sha(rpath),
               # the receipt's own date when it carries one, else the date it was last committed
               "measured_at": (data.get("measured_at") if isinstance(data, dict) else None)
               or _committed(receipt) or "uncommitted"}
        row.update(extract(data))
        rows.append(row)
    return {"kind": "inspeximus.robustness_evidence/1",
            "note": "Measurements of the library, carried into compliance_report() as dated evidence rows. "
                    "Each names the probe that recomputes it and the sha256 of its receipt; a receipt that no "
                    "longer hashes to this reads STALE in the report. They describe the library, not any "
                    "particular store, and they are not a certification.",
            "rows": rows}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    doc = build()
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if "--check" in argv:
        try:
            with open(OUT, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = ""
        if current != text:
            print("STALE: %s does not match the receipts in this tree; run tools/gen_robustness_evidence.py" % OUT)
            return 1
        print("robustness evidence: %d rows match their receipts" % len(doc["rows"]))
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, OUT)
    print("wrote %s: %d rows" % (OUT, len(doc["rows"])))
    for r in doc["rows"]:
        print("  %-10s %-12s %s = %s" % (r["id"], r["measured_at"], r["metric"][:50], r["value"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
