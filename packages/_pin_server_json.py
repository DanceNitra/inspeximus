"""Pin every version-carrying manifest to the version we actually released.

Run as:  python packages/_pin_server_json.py 1.28.1
Or with no argument, to take the version from pyproject.toml.

The MCP registry stores only metadata and resolves the package from PyPI, so a server.json left behind at
an older version advertises a listing that points at something else. Ours sat at 1.24.4 while 1.28.0 was
the released package, which is exactly the drift this removes from human hands.

The Claude Code plugin manifests have the same failure mode and were NOT covered: `.claude-plugin/
plugin.json` and `marketplace.json` sat at 1.25.0 while the package was 1.78.0 -- fifty-two releases of
drift, because nothing pinned them and a human had to remember. Fixing the instance would have left the
class alive, so they are pinned here too.
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


def _write(path: pathlib.Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    """Pin every manifest that carries the PACKAGE version.

    The docstring above has claimed since 1.78.0 that the two Claude Code manifests are pinned here
    too. They were not -- this function only ever touched `server.json`, and the guard test was
    satisfied by the claim rather than the behaviour: it greps this file for the strings
    "plugin.json" and "marketplace.json", which the docstring supplies. So the pinner said it covered
    them, the test agreed, and 1.86.0 shipped with both still reading 1.85.0. Asserting a spelling
    instead of an outcome is how a guard passes over the thing it guards.

    `marketplace.json` carries TWO different versions: a top-level one that is the marketplace
    SCHEMA's own version (1.0.0) and one per plugin entry. Only the per-plugin entries are the
    package; rewriting the schema version would corrupt the manifest to fix a lie about it.
    """
    version = argv[1] if len(argv) > 1 and argv[1] else version_from_pyproject()

    p = ROOT / "server.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["version"] = version
    for pkg in d.get("packages", []):
        pkg["version"] = version
    _write(p, d)
    print(f"server.json pinned to {version}")

    p = ROOT / ".claude-plugin" / "plugin.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        d["version"] = version
        _write(p, d)
        print(f"plugin.json pinned to {version}")

    p = ROOT / ".claude-plugin" / "marketplace.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        for entry in d.get("plugins", []):        # NOT d["version"] -- that is the schema's own
            entry["version"] = version
        _write(p, d)
        print(f"marketplace.json pinned to {version} ({len(d.get('plugins', []))} plugin entries)")

    # THE THIRD INSTANCE OF THE SAME CLASS, found on the 1.89.0 release. Bumping pyproject.toml and
    # running this pinner left `inspeximus/core.py` at 1.88.1 -- and core.py's `__version__` is what
    # `import inspeximus; inspeximus.__version__` returns, i.e. the number a USER reads. It is also
    # what `test_the_package_version_is_the_one_source_of_truth` compares against pyproject, so the
    # release was caught here rather than in the wheel; without that test it would have shipped a
    # package announcing the previous version. The README badge had drifted the same way. Two more
    # places a human had to remember, in a file whose entire docstring is about not making a human
    # remember. Pin them.
    # A MISSING FILE and a PRESENT FILE WITH NO VERSION are different situations and are treated
    # differently on purpose. The first is a harness pointing ROOT at a partial copy; the second is
    # the assignment having been renamed or removed under us, which would make this pinner silently
    # cover nothing -- the exact failure the docstring above describes twice. So: skip the first with
    # a line that says so, refuse the second loudly.
    p = ROOT / "inspeximus" / "core.py"
    if not p.exists():
        print("core.py not present under this ROOT; nothing pinned there")
    else:
        text = p.read_text(encoding="utf-8")
        new, n = re.subn(r'^__version__ = "[^"]+"$', f'__version__ = "{version}"',
                         text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit("::error::core.py has no single __version__ assignment to pin; "
                             "refusing to guess where the version lives")
        if new != text:
            p.write_text(new, encoding="utf-8")
        print(f"core.py __version__ pinned to {version}")

    # The README badge is the first version a reader sees, and it is prose, so it gets a NARROW
    # pattern: only the standalone `v<semver>` token, never a bare number that might be a citation,
    # a DOI fragment or a dependency bound. If the token is absent the pinner says so and moves on
    # rather than inventing a place to write.
    p = ROOT / "README.md"
    if p.exists():
        text = p.read_text(encoding="utf-8")
        new, n = re.subn(r'(?<![\w.])v\d+\.\d+\.\d+(?![\w.])', f"v{version}", text)
        if n:
            if new != text:
                p.write_text(new, encoding="utf-8")
            print(f"README.md version badge pinned to v{version} ({n} occurrence(s))")
        else:
            print("README.md carries no vX.Y.Z badge; nothing pinned there")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
