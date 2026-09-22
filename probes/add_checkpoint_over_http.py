"""Submit 25 growing checkpoints to a live witness over HTTP and measure what it costs.

This is the acceptance for the witness product: somebody else's log hands us a checkpoint over the
wire and gets a cosignature back. It runs the real server in a thread, the real client over TCP, and
the real Ed25519 on both sides. Nothing here is mocked, because the number is the point.

    python probes/add_checkpoint_over_http.py

Prints one JSON line: p50 and p90 in milliseconds over the accepted calls, the payload sizes, and
three controls that must refuse. Exits non-zero if a control passes or the latency is missing.

THE CONTROLS ARE WHY THIS IS A PROBE AND NOT A DEMO. A witness that answers 200 to everything is
fast and worthless, so the run also submits a fork, a rollback and a checkpoint signed by a stranger,
and requires 422, 409 and 403.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import checkpoint as cp                                   # noqa: E402
from inspeximus import merkle                                            # noqa: E402
from inspeximus import witness_checkpoint as wc                          # noqa: E402
from inspeximus.core import new_receipt_keypair                          # noqa: E402

ORIGIN = "customer.example/log"
N_CALLS = 25


def main() -> int:
    import http.server
    import socketserver
    import tempfile

    log_sk, log_pub = new_receipt_keypair()
    w_sk, w_pub = new_receipt_keypair()
    state = os.path.join(tempfile.mkdtemp(prefix="witness-probe-"), "state.json")
    witness = wc.CheckpointWitness(state, {ORIGIN: log_pub}, "witness.example/w1", w_sk, w_pub,
                                   max_per_minute=10_000)

    class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Threaded(("127.0.0.1", 0), wc.make_handler(witness))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/add-checkpoint" % port

    leaves = []
    latencies, sizes, cosig_bytes = [], [], []
    try:
        for i in range(N_CALLS):
            old = len(leaves)
            leaves.append(b"entry %d" % i)
            note = cp.signed_checkpoint(ORIGIN, len(leaves), merkle.root(leaves), log_sk, log_pub)
            proof = merkle.consistency_proof(leaves, old) if old else []
            body = wc.build_request(old, proof, note)
            t0 = time.perf_counter()
            out = wc.submit(url, note, old, proof)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            sizes.append(len(body))
            cosig_bytes.append(len(out["cosignatures"].encode()))
            assert out["status"] == 200, out

        # the cosignature the last call returned has to verify against the witness's public key
        last_note = cp.signed_checkpoint(ORIGIN, len(leaves), merkle.root(leaves), log_sk, log_pub)
        text = cp.split_note(last_note)[0]
        ts = wc.verify_cosignature(text, out["cosignatures"].strip(), witness.name, w_pub)

        controls = {}
        # a fork at a larger size
        forked = list(leaves)
        forked[2] = b"rewritten"
        forked.append(b"extra")
        note = cp.signed_checkpoint(ORIGIN, len(forked), merkle.root(forked), log_sk, log_pub)
        try:
            wc.submit(url, note, len(leaves), merkle.consistency_proof(leaves + [b"extra"], len(leaves)))
            controls["fork_refused"] = False
        except wc.Refused as r:
            controls["fork_refused"] = r.status == 422
        # a rollback
        small = cp.signed_checkpoint(ORIGIN, 5, merkle.root(leaves[:5]), log_sk, log_pub)
        try:
            wc.submit(url, small, 5, [])
            controls["rollback_refused"] = False
        except wc.Refused as r:
            controls["rollback_refused"] = r.status == 409
        # a stranger's key
        s_sk, s_pub = new_receipt_keypair()
        impostor = cp.signed_checkpoint(ORIGIN, len(leaves) + 1,
                                        merkle.root(leaves + [b"x"]), s_sk, s_pub)
        try:
            wc.submit(url, impostor, len(leaves), merkle.consistency_proof(leaves + [b"x"], len(leaves)))
            controls["stranger_refused"] = False
        except wc.Refused as r:
            controls["stranger_refused"] = r.status == 403
    finally:
        httpd.shutdown()

    latencies.sort()
    res = {
        "probe": os.path.basename(__file__),
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": len(latencies),
        "p50_ms": round(statistics.median(latencies), 2),
        "p90_ms": round(latencies[int(0.9 * (len(latencies) - 1))], 2),
        "min_ms": round(latencies[0], 2),
        "max_ms": round(latencies[-1], 2),
        "request_bytes_min": min(sizes), "request_bytes_max": max(sizes),
        "cosignature_bytes": cosig_bytes[-1],
        "final_tree_size": len(leaves),
        "cosignature_timestamp": ts,
        "controls": controls,
        "scope": ("Loopback HTTP on one machine: this measures the witness, not a network. A real "
                  "customer adds their round trip and TLS on top."),
    }
    print(json.dumps(res))
    return 0 if all(controls.values()) and res["p50_ms"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
