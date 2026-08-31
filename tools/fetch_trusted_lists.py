#!/usr/bin/env python
"""Build an offline cache of the EU qualified timestamp services, with its provenance.

    python tools/fetch_trusted_lists.py --out inspeximus_trusted_lists.json

Starts at the Commission's List of Trusted Lists, follows the MACHINE-READABLE pointer for each
member state, and keeps the services of type qualified timestamp with every status they have held.

WHY THE MIME TYPE MATTERS, since selecting on it looks like a detail. Each country is pointed at
twice, once as XML and once as a PDF for human readers, under the same SchemeTerritory. Taking the
first pointer per territory fetched the PDF for 9 of 31 countries, France and Spain among them.
Nothing failed: HTTP 200, a 400 KB file, and zero qualified services parsed out of it. The output
would have said France lists no qualified timestamp authority, in the same shape as a real answer.

WHAT IT RECORDS ABOUT ITSELF. Every territory's URL, fetch time and the SHA-256 of the bytes parsed,
plus every territory it could NOT reach. A verifier reading a cached verdict can re-fetch and confirm
the digest. A cache that did not carry this would be an unsourced claim about who is qualified in
Europe, which is exactly the kind of claim this package exists to refuse.

Reaching every member state is not guaranteed: on 2026-08-31, Hungary's list served a certificate its
own chain did not validate. That is recorded as unreachable rather than dropped, because a check that
silently covers 24 of 25 countries reports a clean absence for the twenty-fifth.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from inspeximus.trusted_list import LOTL_URL, fetch  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="inspeximus_trusted_lists.json",
                    help="where to write the cache (default: %(default)s)")
    ap.add_argument("--lotl", default=LOTL_URL)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a, flush=True))
    started = time.time()
    trusted, provenance = fetch(args.lotl, args.timeout, args.workers, progress=say)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(trusted.to_cache(provenance), fh, separators=(",", ":"), sort_keys=True)

    reached, missing = len(provenance["territories"]), len(provenance["unreachable"])
    changing = sum(1 for s in trusted.services if len({x.kind for x in s.statuses}) > 1)
    say("")
    say("wrote %s  (%.1f MB, %.0fs)" % (args.out, os.path.getsize(args.out) / 1e6,
                                        time.time() - started))
    # Reported separately on purpose. Five lists parse cleanly and hold no qualified timestamp
    # service at all, so "1477 services from 30 territories" would credit five territories with
    # coverage they do not provide, and an absence in one of them would read as a checked absence.
    say("  %d qualified timestamp services, from %d of the %d lists parsed"
        % (len(trusted), len(trusted.territories), reached))
    say("  %d of them (%.0f%%) have held both a qualified and a non-qualified status"
        % (changing, 100.0 * changing / max(1, len(trusted))))
    if missing:
        say("  %d territories NOT reached, so an absence is not an EU-wide absence: %s"
            % (missing, ", ".join(sorted(provenance["unreachable"]))))
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
