"""End-state test for migrate_mem0.py — runs standalone: python tests/test_migrate_mem0.py

Builds a synthetic mem0 history DB with the EXACT schema of mem0 OSS
(mem0/memory/storage.py `_create_history_table`) and a live-export JSON, then
asserts the tool reconstructs correction chains, honours deletions, reconciles
against the live export, and imports chain-less memories unkeyed.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from migrate_mem0 import load_current, migrate, read_history  # noqa: E402

SCHEMA = """
CREATE TABLE history (
    id           TEXT PRIMARY KEY,
    memory_id    TEXT,
    old_memory   TEXT,
    new_memory   TEXT,
    event        TEXT,
    created_at   DATETIME,
    updated_at   DATETIME,
    is_deleted   INTEGER,
    actor_id     TEXT,
    role         TEXT
)
"""

# ids
A, B, C = "mem-aaa", "mem-bbb", "mem-ccc"
EVENTS = [
    # Chain A: ADD -> UPDATE -> noop UPDATE -> DELETE  (ends deleted; absent from live export)
    ("h1", A, None, "sky is blue", "ADD", "2026-01-01T10:00:00+00:00", None, 0, None, None),
    ("h2", A, "sky is blue", "sky is green", "UPDATE", "2026-01-02T10:00:00+00:00", None, 0, None, None),
    ("h3", A, "sky is green", "sky is green", "UPDATE", "2026-01-03T10:00:00+00:00", None, 0, None, None),
    ("h4", A, "sky is green", None, "DELETE", "2026-01-04T10:00:00+00:00", None, 0, None, None),
    # Chain B: ADD -> UPDATE (ends live as the current value)
    ("h5", B, None, "theme is dark", "ADD", "2026-01-01T11:00:00+00:00", None, 0, None, None),
    ("h6", B, "theme is dark", "theme is light", "UPDATE", "2026-01-02T11:00:00+00:00", None, 0, None, None),
    # Chain C: ADD only; live store has since moved on -> reconcile
    ("h7", C, None, "city is Nitra", "ADD", "2026-01-01T12:00:00+00:00", None, 0, None, None),
]

CURRENT = {
    "results": [
        {"id": B, "memory": "theme is light", "user_id": "u1",
         "created_at": "2026-01-02T11:00:00+00:00"},
        {"id": C, "memory": "city is Bratislava", "user_id": "u1",
         "created_at": "2026-01-01T12:00:00+00:00"},
        {"id": "mem-plain", "memory": "a plain unkeyed fact", "user_id": "u1",
         "created_at": "2026-01-05T09:00:00+00:00"},
    ]
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="migrate_test_"))
    db = tmp / "history.db"
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO history (id, memory_id, old_memory, new_memory, event, created_at, "
        "updated_at, is_deleted, actor_id, role) VALUES (?,?,?,?,?,?,?,?,?,?)", EVENTS)
    con.commit()
    con.close()

    current_json = tmp / "current.json"
    current_json.write_text(json.dumps(CURRENT), encoding="utf-8")
    store_path = tmp / "migrated.json"

    events, hnotes = read_history(str(db))
    assert len(events) == 7, f"expected 7 events, got {len(events)}"
    current, _ = load_current(str(current_json))
    report = migrate(events, current, str(store_path))

    # ---- report shape
    assert report["events"] == 7 and report["chains"] == 3
    assert report["superseded_events"] == 2, report
    assert report["noop_events"] == 1, report          # green -> green replay is a no-op
    assert report["deleted_events"] == 1, report
    assert report["reconciled"] == 1, report           # chain C drifted from the live store
    assert report["chain_end_missing"] == 1, report    # chain A ends deleted
    assert report["unkeyed"] == 1, report              # plain fact has no chain
    assert report["chains_imported"] == 2, report      # B and C active after reconcile
    assert report["reconcile"][0]["memory_id"] == C

    # ---- end state in the store
    from inspeximus import Inspeximus
    store = Inspeximus(str(store_path))

    hits_b = store.recall("what theme is it", k=5)
    texts_b = [h.get("text", "") for h in hits_b if isinstance(h, dict)]
    assert any("light" in t for t in texts_b), texts_b
    assert not any("dark" in t for t in texts_b), f"stale value surfaced: {texts_b}"

    hits_c = store.recall("which city", k=5)
    texts_c = [h.get("text", "") for h in hits_c]
    assert any("Bratislava" in t for t in texts_c), texts_c

    hits_a = store.recall("what color is the sky", k=5)
    texts_a = [h.get("text", "") for h in hits_a]
    assert not any("blue" in t or "green" in t for t in texts_a), \
        f"deleted chain surfaced: {texts_a}"

    hits_p = store.recall("plain unkeyed fact", k=5)
    texts_p = [h.get("text", "") for h in hits_p]
    assert any("plain unkeyed fact" in t for t in texts_p), texts_p

    # ---- supersession ledger knows the correction
    try:
        hist = store.history(f"mem0:{B}")
        values = json.dumps(hist, ensure_ascii=False)
        assert "dark" in values and "light" in values, values
    except Exception as e:
        raise AssertionError(f"history() unavailable or failed: {e}")

    print(json.dumps({
        "result": "PASS",
        "store": str(store_path),
        "report": {k: report[k] for k in
                   ("events", "chains", "chains_imported", "superseded_events",
                    "noop_events", "deleted_events", "reconciled",
                    "chain_end_missing", "unkeyed")},
        "integrity": report.get("integrity"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

