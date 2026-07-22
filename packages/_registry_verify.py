"""Confirm a given version is actually listed in the MCP registry.

    curl -s "https://registry.modelcontextprotocol.io/v0.1/servers?search=inspeximus" -o r.json
    python packages/_registry_verify.py r.json 1.29.0     # exit 0 if listed, 1 if not

The registry keeps every published version and does NOT return them newest-first, so a check that reads
`servers[0]` reads the OLDEST entry. This confirms the requested version is present among all of them, and
reports which one the registry marks latest.
"""
import json
import pathlib
import sys

NAME = "io.github.DanceNitra/inspeximus"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    payload = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    want = argv[2]

    entries = [e for e in payload.get("servers", []) if e.get("server", {}).get("name") == NAME]
    have = {e["server"]["version"] for e in entries}
    latest = next((e["server"]["version"] for e in entries
                   if e.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {}).get("isLatest")),
                  "?")
    if want in have:
        print(f"listed: {want} present; registry latest = {latest}")
        return 0
    print(f"not yet: {want} not among {sorted(have) or 'nothing'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
