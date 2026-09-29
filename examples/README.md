# inspeximus examples

Runnable, copy-paste examples. Each is self-contained and needs only `pip install inspeximus`.

| file | shows |
|---|---|
| [`01_basics.py`](01_basics.py) | remember, recall, first-class **correction** (keyed supersession), and `history()` audit trail |
| [`02_correction_and_erasure.py`](02_correction_and_erasure.py) | `echo_guard` (a restated stale value doesn't resurrect), `forget()`, and audited `forget_subject()` erasure |
| [`03_semantic_recall.py`](03_semantic_recall.py) | plug **any** `embed=` function for semantic recall; runs as-is with a dependency-free stand-in |
| [`04_encryption.py`](04_encryption.py) | AES-256-GCM **encryption-at-rest** + **crypto-shredding** erasure (needs `cryptography`) |
| [`06_gdpr_erasure_receipt.py`](06_gdpr_erasure_receipt.py) | **signed erasure receipt** (GDPR Art. 17 / EU AI Act Art. 12): Ed25519-signed, hash-chained, content-free tombstones grouped by DSAR request id, provable end-to-end via `verify_writes()` + `governance_report()` |
| [`11_verifiable_erasure.py`](11_verifiable_erasure.py) | the **three-command path** of [docs/ERASURE.md](../docs/ERASURE.md) end to end — delete, signed certificate, independent residue scan — with the control that makes it mean anything: the deleted record is gone **and** a different one is still there, and a tampered certificate FAILS |
| [`12_split_view_detection.py`](12_split_view_detection.py) | **split-view detection**: a co-signed RFC-6962 tree head, three witnesses refusing a fork, the proof when one is tricked into signing both — with the controls (tampered anchor must FAIL, identical heads must stay SILENT). Quickstart: [`docs/TRANSPARENCY.md`](../docs/TRANSPARENCY.md) |

```bash
pip install inspeximus
python 01_basics.py
```

Everything here is zero-dependency and needs no LLM or API key. For semantic recall at production quality,
`pip install sentence-transformers` and pass its encoder as `embed=` (see `03_semantic_recall.py`).
