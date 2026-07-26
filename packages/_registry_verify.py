"""Confirm a given version is actually listed in the MCP registry.

    python packages/_registry_verify.py 1.29.0             # exit 0 if listed, 1 if not
    python packages/_registry_verify.py r.json 1.29.0      # or check a payload already on disk

The registry keeps every published version and does NOT return them newest-first, so a check that reads
`servers[0]` reads the OLDEST entry. This confirms the requested version is present among all of them, and
reports which one the registry marks latest.

It also PAGES. The response caps at 30 entries and hands back a `nextCursor`, so once we had published more
than 30 versions a single-page check could only ever see the oldest 30 — from 1.57.0 on it reported "not
listed" for a version that was listed, and failed the release job every time. A paginated API read without
a cursor loop is a check that silently stops covering what it claims to cover.
"""
import urllib.request
import json
import pathlib
import sys
import urllib.parse

NAME = "io.github.DanceNitra/inspeximus"
BASE = "https://registry.modelcontextprotocol.io/v0.1/servers?search=inspeximus"
MAX_PAGES = 200                                  # a cursor that never terminates must not hang the job


def fetch_all() -> list:
    """Every page of the search, following `nextCursor` to the end."""
    entries, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        url = BASE + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        with urllib.request.urlopen(url, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
        entries += payload.get("servers", [])
        cursor = (payload.get("metadata") or {}).get("nextCursor")
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return entries


def main(argv: list[str]) -> int:
    if len(argv) == 2:                           # fetch mode: page through the live registry
        want, servers = argv[1], fetch_all()
    elif len(argv) == 3:                         # file mode: a payload already on disk (one page only)
        want = argv[2]
        servers = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8")).get("servers", [])
    else:
        print(__doc__)
        return 2

    entries = [e for e in servers if e.get("server", {}).get("name") == NAME]
    have = {e["server"]["version"] for e in entries}
    latest = next((e["server"]["version"] for e in entries
                   if e.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {}).get("isLatest")),
                  "?")
    if want in have:
        print(f"listed: {want} present among {len(have)} versions; registry latest = {latest}")
        return 0
    print(f"not yet: {want} not among {len(have)} listed versions "
          f"(newest few: {sorted(have)[-4:] or 'nothing'})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
