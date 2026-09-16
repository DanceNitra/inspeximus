"""The Dockerfile installs the package by the name PyPI serves. It read `agora-inspeximus` for eight weeks
after the 1.26.0 rename, a name that 404s, so every registry build that ran it failed (Glama, 2026-09-16)
and nothing in the suite noticed, because nothing read the file. Control: the pyproject name is the one the
Dockerfile installs, and the old name appears nowhere in it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_dockerfile_installs_the_pyproject_name():
    name = re.search(r'^name = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    installs = re.findall(r'pip install[^\n]*?"([A-Za-z0-9_.-]+)(\[[^\]]*\])?"', docker)
    assert installs, "the Dockerfile has no pip install line"
    assert all(pkg == name for pkg, _extra in installs), installs
    assert "agora-inspeximus" not in docker.replace("# ", "#").split("FROM")[1]   # the comment may name the old name; the RUN may not
