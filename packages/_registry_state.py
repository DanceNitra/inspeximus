"""Is this exact server version already in the MCP registry?

    curl -s "https://registry.modelcontextprotocol.io/v0.1/servers?search=inspeximus" -o reg.json
    python packages/_registry_state.py reg.json      # prints and writes already=true|false

Prints `already=true|false` and appends it to `$GITHUB_OUTPUT`. The registry refuses a duplicate version
with a 400, which is correct on its side but made the publish step fail on any re-run, including a re-run
of a release that had already succeeded. Asking first turns "it is already listed" into the success it is,
without making the publish step swallow errors it should not.

The response is fetched by the caller with curl rather than here with `urllib`: urllib requests to this
host time out, both on a GitHub runner and locally, while curl and the Go publisher both succeed. Rather
than guess at their edge, the fetch uses the client that demonstrably works.

BUT that curl fetches ONE page, and the registry caps a page at 30 entries with a `nextCursor`. Past 30
published versions the file could no longer contain ours, so this would answer "not listed" for a version
that was listed and send publish into the duplicate-version 400 it exists to avoid. So we now try to page
the whole search ourselves and fall back to the curl'd file if that fetch fails — keeping the client that
demonstrably works as the floor, while covering every page when we can reach them.
"""
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

BASE = "https://registry.modelcontextprotocol.io/v0.1/servers?search=inspeximus"

ROOT = pathlib.Path(__file__).resolve().parents[1]


def listed_versions(payload: dict, name: str) -> set[str]:
    # The registry keeps every published version, so this must collect ALL of them, not just the first
    # match. Returning the first entry's version (which is the OLDEST) would compare an old version to
    # ours, decide "not listed", and then hit the duplicate-version 400 the check exists to avoid.
    return {(e.get("server", e)).get("version")
            for e in payload.get("servers", [])
            if (e.get("server", e)).get("name") == name}


def fetch_all_pages() -> list:
    """Every page of the search, following `nextCursor`. Raises if the host is unreachable."""
    entries, cursor, seen = [], None, set()
    for _ in range(200):
        url = BASE + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        with urllib.request.urlopen(url, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
        entries += payload.get("servers", [])
        cursor = (payload.get("metadata") or {}).get("nextCursor")
        if not cursor or cursor in seen:
            return entries
        seen.add(cursor)
    return entries


def main(argv: list[str]) -> int:
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    name, version = manifest["name"], manifest["version"]

    versions: set[str] = set()
    try:
        versions = listed_versions({"servers": fetch_all_pages()}, name)
        print(f"paged the registry directly: {len(versions)} versions")
    except Exception as e:
        print(f"could not page the registry ({type(e).__name__}); falling back to the curl'd response")

    if versions:
        pass
    elif len(argv) > 1:
        try:
            versions = listed_versions(json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8")), name)
        except Exception as e:
            # An unreadable response is not evidence that the version is absent; let publish decide, and
            # let it fail loudly if the version really is a duplicate.
            print(f"could not read the registry response ({type(e).__name__}); assuming not listed")
    else:
        print("no registry response given; assuming not listed")

    already = version in versions
    print(f"{name}: registry has {sorted(versions) or 'nothing'}, we want {version!r} -> already={already}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"already={'true' if already else 'false'}\n")
            fh.write(f"version={version}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
