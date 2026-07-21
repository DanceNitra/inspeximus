"""Pin server.json's versions to the version we actually released.

Run as:  python packages/_pin_server_json.py 1.28.1
Or with no argument, to take the version from pyproject.toml.

The MCP registry stores only metadata and resolves the package from PyPI, so a server.json left behind at
an older version advertises a listing that points at something else. Ours sat at 1.24.4 while 1.28.0 was
the released package, which is exactly the drift this removes from human hands.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def version_from_pyproject() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("::error::no version found in pyproject.toml")
    return m.group(1)


def main(argv: list[str]) -> int:
    version = argv[1] if len(argv) > 1 and argv[1] else version_from_pyproject()
    p = ROOT / "server.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["version"] = version
    for pkg in d.get("packages", []):
        pkg["version"] = version
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"server.json pinned to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
