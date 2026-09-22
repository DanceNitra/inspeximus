"""Art. 15 evidence row: a split-view (two histories shown to two readers) is detected from two
co-signed heads, and an honest append-only pair is not.

The measurement `compliance_report()` carries for the operator-side tampering half of Art. 15
(robustness against attempts to alter behaviour). Two stores are built from one history: an honest
one that appends, and a forked copy that rewrites the same position with a different record. One
witness co-signs a head of each. `detect_split_view` must report the fork on the inconsistent pair
(same log size, different tip) and must not report one on the honest pair (the control).

Deterministic: no LLM, no embedder, no network. Runs in under a second. The receipt is
`.result.json` beside this file, and `compliance_report()` names its sha256, so a receipt edited
after the fact reads STALE there.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inspeximus import Inspeximus, core  # noqa: E402
from _receipt import write_receipt  # noqa: E402


def main():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    sk_hex, pub = sk.private_bytes_raw().hex(), sk.public_key().public_bytes_raw().hex()

    d = tempfile.mkdtemp()
    honest_path = os.path.join(d, "honest.json")
    honest = Inspeximus(honest_path, receipts=True)
    honest.remember("the deploy region is eu-central-1", key="region", object="eu-central-1")
    head_shared = honest.anchor()
    honest.flush()
    # the fork: a copy of the same store at the same point, then a DIFFERENT next record
    fork_path = os.path.join(d, "fork.json")
    shutil.copyfile(honest_path, fork_path)
    for side in os.listdir(d):
        if side.startswith("honest.json.") and not side.endswith(".lock"):
            shutil.copyfile(os.path.join(d, side), os.path.join(d, side.replace("honest.json.", "fork.json.")))
    honest.remember("the on-call rota moved to Thursday", key="rota", object="thursday")
    head_honest = honest.anchor()
    fork = Inspeximus(fork_path, receipts=True)
    fork.remember("the deploy region is us-east-1", key="region", object="us-east-1")
    head_fork = fork.anchor()

    cos = lambda h: (pub, core.witness_cosign(sk_hex, h))  # noqa: E731
    forked = Inspeximus.detect_split_view(head_honest, [cos(head_honest)], head_fork, [cos(head_fork)], witnesses=[pub])
    control = Inspeximus.detect_split_view(head_shared, [cos(head_shared)], head_honest, [cos(head_honest)], witnesses=[pub])
    out = {
        "probe": os.path.basename(__file__),
        "inspeximus": core.__version__,
        "measured_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d"),
        "property": "split-view detection from two witness co-signed heads",
        "forked_pair": {"fork": forked["fork"], "inconsistent": forked["inconsistent"],
                        "both_cosigned": forked["both_cosigned"],
                        "n_writes": [head_honest.get("n_writes"), head_fork.get("n_writes")]},
        "honest_pair_control": {"fork": control["fork"], "both_cosigned": control["both_cosigned"]},
        "verdict": "DETECTED" if (forked["fork"] and forked["both_cosigned"] and not control["fork"]
                                  and control["both_cosigned"]) else "FAILED",
    }
    print(json.dumps(out, indent=1))
    write_receipt(__file__, out)   # not under the suite: the committed receipt is the cited number
    assert out["verdict"] == "DETECTED", out
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
