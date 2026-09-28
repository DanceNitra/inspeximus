"""Every install line on the page carries `inspeximus[mcp]>=<this version>` (3.15.6, PC2 friend flow G1).

On a friend's machine Hermes Agent ran the page's upgrade line without `-U`; pip answered "Requirement already
satisfied" with 3.14.3, and the old installer ran again. A floor makes pip upgrade with or without `-U`. It
must equal the version released, so a floor that lags installs the previous release: tools/release_check.py
checks it as a version carrier, and this test checks it on every run.
"""
import os
import re
import subprocess
import sys

import inspeximus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "docs", "install", "index.md")


def _install_lines(text):
    return [ln for ln in text.splitlines() if "-m pip install" in ln and "inspeximus[mcp]" in ln]


def test_every_install_line_names_this_version_as_its_floor():
    lines = _install_lines(open(PAGE, encoding="utf-8").read())
    assert len(lines) == 3, lines                                   # posix, PowerShell, Git Bash
    for ln in lines:
        m = re.search(r'"inspeximus\[mcp\]>=([0-9][0-9A-Za-z.+-]*)"', ln)
        assert m and m.group(1) == inspeximus.__version__, (ln, inspeximus.__version__)


def test_release_check_fails_a_lagging_floor(tmp_path):
    """Control: the carrier reader sees the floor, so a stale one is a FAIL, not a silent pass."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import release_check
    finally:
        sys.path.remove(os.path.join(ROOT, "tools"))
    assert {"docs/install/index.md", "install/index.html"} <= set(release_check.REQUIRED_CARRIERS)
    stale = tmp_path / "docs" / "install"
    stale.mkdir(parents=True)
    text = open(PAGE, encoding="utf-8").read().replace(">=" + inspeximus.__version__, ">=0.0.1")
    (stale / "index.md").write_text(text, encoding="utf-8")
    found, err = release_check._read_carrier(tmp_path, "docs/install/index.md")
    assert err is None and [v for _, v in found] == ["0.0.1"] * 3, (found, err)


def test_pip_reads_the_floor_as_a_requirement():
    """The quoted spec is valid pip syntax (a typo here would make every install line fail)."""
    from importlib.metadata import version as _v  # noqa: F401  (stdlib only)
    line = _install_lines(open(PAGE, encoding="utf-8").read())[0]
    spec = re.search(r'"(inspeximus\[mcp\]>=[^"]+)"', line).group(1)
    r = subprocess.run([sys.executable, "-c", "from packaging.requirements import Requirement as R; "
                        "print(R(%r).specifier)" % spec], capture_output=True, text=True)
    if r.returncode != 0 and "No module named 'packaging'" in r.stderr:
        import pytest
        pytest.skip("packaging is not installed here")
    assert r.returncode == 0 and r.stdout.strip() == ">=" + inspeximus.__version__, (r.stdout, r.stderr)
