"""The MCP registry proves ownership by reading a marker out of the README we ship to PyPI.

WHY THIS EXISTS. `ddf47a3` cut the landing page from 1,068 lines to 156 and took the last line with it:

    <!-- MCP registry ownership proof -->
    mcp-name: io.github.DanceNitra/inspeximus

Nothing failed. The tests were green, the audits were green, CI was green, and the marker's absence is
invisible to every one of them because no code reads it. It surfaced one release later, on 2026-08-13,
when v2.6.0 published to PyPI and the registry step returned:

    registry validation failed for package 0 (inspeximus): PyPI package 'inspeximus' ownership
    validation failed. The server name 'io.github.DanceNitra/inspeximus' must appear as
    'mcp-name: io.github.DanceNitra/inspeximus' in the package README

The core shipped; the listing did not. And it could not be repaired for 2.6.0, because the registry reads
the README of the PUBLISHED artifact, so a fix in the tree only takes effect at the NEXT version. A
silent breakage that can only be repaired one release later is worth a test that fails immediately.

The commit that removed it was literally titled "find what a moved document takes with it". It took this.
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _server_name():
    with io.open(os.path.join(ROOT, "server.json"), encoding="utf-8") as fh:
        return json.load(fh)["name"]


def _packaged_readme_path():
    """The file pyproject actually ships as the long description.

    Asserted rather than assumed. A guard that hardcodes README.md keeps passing on the day someone
    points `readme =` at a different file, which is the same defect this whole file is about: the
    document moved and its guard stayed where it was.
    """
    # Read with a regex rather than tomllib, deliberately. A module-level `pytest.importorskip` for the
    # 3.9 tomli fallback made the skip census count this entire file as invisible to the base CI job --
    # and a guard that does not run in the job that gates publish is the exact defect it exists to
    # prevent. One `readme = "..."` line, asserted to be unique, needs no parser.
    text = io.open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    hits = re.findall(r"""(?m)^\s*readme\s*=\s*["']([^"']+)["']""", text)
    assert len(hits) == 1, (
        "expected exactly one `readme =` line in pyproject.toml, found %d (%r). More than one and this "
        "guard cannot tell which document ships." % (len(hits), hits))
    rel = hits[0]
    path = os.path.join(ROOT, rel)
    assert os.path.exists(path), f"pyproject ships {rel!r} as the long description and it does not exist"
    return path


def test_the_packaged_readme_carries_the_registry_ownership_marker():
    name = _server_name()
    text = io.open(_packaged_readme_path(), encoding="utf-8").read()
    assert re.search(r"(?m)^mcp-name:\s*%s\s*$" % re.escape(name), text), (
        "the README shipped to PyPI must contain a line reading exactly `mcp-name: %s`, or the MCP "
        "registry refuses the listing on ownership validation. It went missing in ddf47a3 and cost the "
        "v2.6.0 listing, which cannot be repaired without cutting a new version." % name)


def test_CONTROL_the_marker_is_checked_against_server_json_not_a_hardcoded_string():
    """Without this the test above could pass forever on a stale literal.

    The name is the registry's join key between server.json and the PyPI package. If server.json is
    renamed and the README is not, publishing breaks exactly as it did here, so the guard has to read
    the live value -- and this arm fails if server.json ever stops carrying one.
    """
    name = _server_name()
    assert name and name.startswith("io.github."), (
        "server.json carries no usable registry name (%r), so the marker check above has nothing to "
        "compare against and would be asserting a constant." % name)


def test_CONTROL_a_readme_without_the_marker_is_actually_rejected(tmp_path):
    """The negative control. A regex that matched anything would pass both tests above."""
    name = _server_name()
    pattern = re.compile(r"(?m)^mcp-name:\s*%s\s*$" % re.escape(name))
    assert not pattern.search("# inspeximus\n\nsome prose, no marker\n")
    assert not pattern.search("mcp-name: io.github.SomeoneElse/inspeximus\n"), \
        "another owner's marker must not satisfy our ownership proof"
    assert not pattern.search("please set mcp-name: %s in your readme\n" % name), \
        "the marker must be its own line, not a mention inside a sentence"
    assert pattern.search("intro\n\nmcp-name: %s\n" % name)
