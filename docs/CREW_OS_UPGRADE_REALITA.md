# Inspeximus x CREW OS — co REALNE chyba (overene proti kodu)

> Overene 2026-09-20 priamo v checkoute `inspeximus-repo`, verzia 2.44.0.
> Manual z Notebooku navrhuje 5 modulov; toto je realita. Kazde tvrdenie ma dokaz z kodu.

| Kategoria | Co | Stav | Dokaz |
|---|---|---|---|
| Multi-agent | izolacia a granty medzi agentmi | **EXISTUJE** | core.py: grant/revoke/can_read/as_agent/grants/for_tenant |
| Multi-agent | register_agent + recall_agent_context + grant_cross_agent_access | **CHYBA** | tieto 3 mena sa v kode nevyskytuju - manual ich navrhuje ako nove API |
| Tiered cache | L1 cache v RAM | **CASTOCNE** | reload/refresh/index_coherence existuju; ziadna L1 obalka v repe (overene 2026-09-20: subor s tym menom neexistuje) |
| Tiered cache | TieredMemoryStore (L1/L2/L3 trieda v jadre) | **CHYBA** | v core.py nie je; nasa L1 je mimo jadra, nutena rucne reload |
| Store | row-level SQLite persistencia (WAL-ish diff save) | **EXISTUJE** | inspeximus/sqlite_store.py (18 564 B): SCHEMA records(id,ord,doc)+meta, snapshot/save/migrate_from_json |
| Store | migracia JSON -> SQLite | **EXISTUJE** | sqlite_store.migrate_from_json(json_path, db_path) |
| Store | explicitne PRAGMA journal_mode=WAL | **OVERIT** | v hlavicke sqlite_store.py WAL nie je spomenuty |
| Event bus | zmenove baliky medzi store-mi | **EXISTUJE** | export_changeset/import_changeset/state_digest/witness |
| Event bus | crew_events tabulka + event bus | **CHYBA** | manual navrhuje; v kode nie je |
| Daemon | sleep/consolidacia/retention | **EXISTUJE** | sleep/consolidate/consolidate_clusters/apply_retention |
| Daemon | autonomny demon (crew_daemon.py) | **CHYBA** | nie je - to je vec CREW OS, nie jadra Inspeximusu |
| Self-healing | outcome propagation | **EXISTUJE** | propagate_outcome/credit/ratify/grade |
| Self-healing | rederivacia a zvratenie lineage | **EXISTUJE** | retract_lineage/rederive/revert/submit_revert |
| Self-healing | convergence_report | **EXISTUJE** | core.py: convergence_report(target) |
| Evidence | audit bundle | **EXISTUJE** | audit_bundle.py (72074 B) |
| Evidence | tamper-evidence / receipts / anchors | **EXISTUJE** | verify_writes/anchor/verify_consistency/merkle_root |
| Recall | iterativny recall (multi-hop) | **EXISTUJE** | recall_iterative_start/followup |
| Recall | vektorovy recall | **EXISTUJE** | recall(... embed=...) - treba overit ci je nativny alebo externy |

## Zavery

- **Nedoprogramovavat, co uz existuje.** Manual z Notebooku navrhuje SQLite WAL, multi-agent granty,
  self-healing, sleep/consolidaciu a evidence - vsetko to uz v 2.44.0 je.
- **Realne chyba len 3 veci:** (1) `TieredMemoryStore` v jadre (L1/L2/L3 ako prva trieda), (2) event bus (`crew_events`) pre 22 agentov,
  (3) explicitne overenie/aktivacia WAL v `sqlite_store.py`.
- **`crew_daemon.py` nie je vec jadra Inspeximus** - je to vec CREW OS (orchestrator). Jadro ma dodat
  API, demon ho vola. Toto je hranica, ktora sa nesmie zliat.
