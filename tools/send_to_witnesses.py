#!/usr/bin/env python
"""Send the log's current checkpoint to each configured witness, with a consistency proof.

c2sp.org/tlog-witness puts the log in charge: the log posts each new checkpoint to its witnesses,
with a proof that the tree grew from the size each witness last cosigned. A witness never polls.
This runs on the log host after every publish and needs only outbound HTTPS.

    python tools/send_to_witnesses.py --site /srv/static-log-v2 \\
        --witnesses /etc/inspeximus/witnesses.json --state /var/lib/inspeximus/witnesses-sent.json

`witnesses.json` is a list of {"name": ..., "url": ...}, where url is the submission prefix and
`/add-checkpoint` is appended. An empty list is a valid configuration and sends nothing.

WHAT IT REMEMBERS is the size each witness last cosigned, per URL. It sends only when the log has
grown past that size, and at most once an hour per witness, whether the last request was
cosigned or refused. If the memory is
lost, the witness answers 409 with its own size; that size is remembered and the next run, an hour
later at the earliest, proves from it. There is no retry inside a run.

Each cosigned checkpoint is written to `<site>/cosignatures/<witness>.note`: the checkpoint note
with the witness's line added, a note any verifier reads.

Exit 0 when every configured witness cosigned or was already current, 1 when any refused or could
not be reached. A refusal never moves the remembered size.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import merkle                                          # noqa: E402
from inspeximus.witness_checkpoint import Refused, submit              # noqa: E402


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def _save(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def send(site: str, witnesses: list, state_path: str, timeout: float = 30.0,
         min_interval: float = 3600.0, now: float | None = None) -> dict:
    note = open(os.path.join(site, "checkpoint"), encoding="utf-8").read()
    size = int(note.split("\n")[1])
    rows = [json.loads(ln) for ln in open(os.path.join(site, "log.jsonl"), encoding="utf-8")
            if ln.strip()]
    mtl = [bytes.fromhex(r["leaf_hash"]) for r in rows]
    if len(mtl) != size:
        raise SystemExit("the checkpoint names %d entries and log.jsonl has %d" % (size, len(mtl)))

    def proof(old: int, new: int) -> list:
        return [] if old == 0 else merkle._subproof(old, mtl[:new], True)

    state = _load(state_path, {})
    out = {"size": size, "results": []}
    for w in witnesses:
        url = w["url"].rstrip("/") + "/add-checkpoint"
        old = int(state.get(w["url"], {}).get("size", 0))
        if old == size:
            out["results"].append({"witness": w["name"], "result": "current", "size": size})
            continue
        # THE DECLARED RATE IS ENFORCED HERE, not promised. A request counts when it is attempted,
        # refused or not, because a witness counts what arrives, not what it cosigned.
        now_ts = time.time() if now is None else now
        last = state.get(w["url"], {}).get("attempted_ts")
        if last is not None and now_ts - float(last) < min_interval:
            out["results"].append({"witness": w["name"], "result": "waiting",
                                   "next_in_s": int(min_interval - (now_ts - last))})
            continue
        state.setdefault(w["url"], {})["attempted_ts"] = now_ts
        _save(state_path, state)
        try:
            # No retry inside a run: a 409 is answered in the NEXT window, so a witness never sees
            # more than one request from us per hour, including while we resynchronise.
            got = submit(url, note, old, proof(old, size), timeout=timeout, fetch_proof=None)
        except Refused as e:
            theirs = str(e.reason).strip()
            if e.status == 409 and theirs.isdigit() and int(theirs) <= size:
                # The witness knows our log at another size than we remembered. Its answer is the
                # size it last cosigned, so remember that and prove from it next time.
                state[w["url"]]["size"] = int(theirs)
                _save(state_path, state)
                out["results"].append({"witness": w["name"], "result": "resynced", "size": int(theirs)})
                continue
            out["results"].append({"witness": w["name"], "result": "refused", "detail": str(e)})
            continue
        except OSError as e:
            out["results"].append({"witness": w["name"], "result": "unreachable", "detail": str(e)})
            continue
        lines = [ln for ln in got["cosignatures"].splitlines() if ln.strip()]
        os.makedirs(os.path.join(site, "cosignatures"), exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", w["name"])
        with open(os.path.join(site, "cosignatures", safe + ".note"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(note.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
        state[w["url"]] = {"size": size, "attempted_ts": now_ts,
                           "cosigned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _save(state_path, state)
        out["results"].append({"witness": w["name"], "result": "cosigned", "size": size})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", required=True)
    ap.add_argument("--witnesses", required=True)
    ap.add_argument("--state", required=True)
    a = ap.parse_args(argv)
    witnesses = _load(a.witnesses, [])
    if not witnesses:
        print("no witnesses configured; nothing sent")
        return 0
    out = send(a.site, witnesses, a.state)
    for r in out["results"]:
        print(json.dumps(r))
    return 0 if all(r["result"] in ("cosigned", "current", "waiting", "resynced")
                    for r in out["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
