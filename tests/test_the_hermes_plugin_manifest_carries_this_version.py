"""Every Hermes manifest names this release, in both the version and the pip floor.

Nous copies the manifest's version into the plugin-catalog entry a Hermes user reads before
installing. MEASURED 2026-10-29: the manifest was written at 3.23.1 and still said 3.23.1 three
releases later, because it was not on the version-carrier list in tools/release_check.py. It is
now, and this test checks it on every run instead of once per release.

Parametrized over every manifest rather than written once per manifest: the context engine added
on 2026-10-29 carries the same two fields and can drift the same way.
"""
import os
import re
import sys

import pytest

import inspeximus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELS = ("integrations/hermes-plugin-inspeximus/plugin.yaml",
        "integrations/hermes-context-engine-inspeximus/plugin.yaml")


def _manifest(rel):
    return os.path.join(ROOT, *rel.split("/"))


def _release_check():
    tools = os.path.join(ROOT, "tools")
    sys.path.insert(0, tools)
    try:
        import release_check
        return release_check
    finally:
        sys.path.remove(tools)


@pytest.mark.parametrize("rel", RELS)
def test_the_manifest_names_this_version_and_this_floor(rel):
    text = open(_manifest(rel), encoding="utf-8").read()
    assert re.search(r'^version:\s*"%s"\s*$' % re.escape(inspeximus.__version__), text, re.M), text
    floors = re.findall(r"inspeximus>=([0-9][0-9A-Za-z.+-]*)", text)
    assert floors == [inspeximus.__version__], (floors, inspeximus.__version__)


@pytest.mark.parametrize("rel", RELS)
def test_the_manifest_is_a_version_carrier(rel):
    """Without this the drift above comes back: nothing else reads the file at release time."""
    assert rel in _release_check().REQUIRED_CARRIERS


@pytest.mark.parametrize("rel", RELS)
def test_release_check_fails_a_lagging_manifest(tmp_path, rel):
    """CONTROL. The reader must see both fields, so a stale one is a FAIL and not a silent pass."""
    rc = _release_check()
    stale = tmp_path / os.path.dirname(rel)
    stale.mkdir(parents=True)
    text = open(_manifest(rel), encoding="utf-8").read().replace(inspeximus.__version__, "0.0.1")
    (stale / "plugin.yaml").write_text(text, encoding="utf-8")
    found, err = rc._read_carrier(tmp_path, rel)
    assert err is None, err
    assert [v for _, v in found] == ["0.0.1", "0.0.1"], found


@pytest.mark.parametrize("rel", RELS)
def test_control_a_manifest_missing_a_field_is_an_error_not_an_empty_pass(tmp_path, rel):
    """A reader that matches nothing reports an error here. An empty list would pass the checklist."""
    rc = _release_check()
    stale = tmp_path / os.path.dirname(rel)
    stale.mkdir(parents=True)
    (stale / "plugin.yaml").write_text("name: inspeximus\n", encoding="utf-8")
    found, err = rc._read_carrier(tmp_path, rel)
    assert not found and err, (found, err)
