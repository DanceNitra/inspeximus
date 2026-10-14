"""3.20 (backlog: "Registry listing check fails a released version"): an unreachable registry is not a missing listing.

THE DEFECT. Release run 38502302165 (3.19.0) was marked failed only because its last step, "the version we just
released must actually be listed", gave up after 20 tries. Every read had timed out (`TimeoutError: The read
operation timed out`), the step counted each as "not indexed yet", and the registry listed 3.19.0 all along.

WHAT IS PINNED HERE. `packages/_registry_verify.py` exits 3 when a read does not reach the registry, apart from 1
(the registry answered and does not list the version). The release step's own loop, run here with a stand-in for the
verifier, fails only when at least one read got an answer, and warns when none did.
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages"))
import _registry_verify as rv  # noqa: E402


def test_a_read_timeout_exits_3(monkeypatch, capsys):
    def boom(*a, **k):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert rv.main(["x", "3.20.0"]) == 3
    assert capsys.readouterr().out.startswith("unreachable:")


def test_a_refused_connection_exits_3(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert rv.main(["x", "3.20.0"]) == 3


def _loop_text():
    yml = open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    step = yml.split("- name: the version we just released must actually be listed", 1)[1]
    body = step.split("run: |", 1)[1].split("\n  github-release:", 1)[0]
    lines = [ln[10:] if ln.startswith(" " * 10) else ln.strip() for ln in body.splitlines()]
    return "\n".join(lines)


def _run_loop(tmp_path, codes):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this machine")
    script = _loop_text()
    assert "_registry_verify.py" in script and "seq 1 20" in script, "CONTROL: the step was found"
    (tmp_path / "codes").write_text("\n".join(str(c) for c in codes) + "\n", encoding="utf-8")
    fake = ('n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count; '
            'code=$(sed -n "${n}p" codes); exit ${code:-1}')
    script = script.replace('VERSION="$(python -c "import json;print(json.load(open(\'server.json\'))[\'version\'])")"',
                            'VERSION=9.9.9')
    script = re.sub(r"python packages/_registry_verify\.py \"\$VERSION\"", "bash ./fake.sh", script)
    script = script.replace("sleep 15", "true")
    (tmp_path / "fake.sh").write_text(fake + "\n", encoding="utf-8", newline="\n")
    (tmp_path / "step.sh").write_text(script + "\n", encoding="utf-8", newline="\n")
    p = subprocess.run([bash, "step.sh"], cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


def test_twenty_unreachable_reads_warn_and_do_not_fail(tmp_path):
    rc, out = _run_loop(tmp_path, [3] * 20)
    assert rc == 0 and "::warning::" in out and "unchecked" in out, out[-400:]


def test_a_registry_that_answers_without_the_version_still_fails(tmp_path):
    rc, out = _run_loop(tmp_path, [3] * 10 + [1] * 10)
    assert rc == 1 and "::error::published, but the registry does not list" in out, out[-400:]


def test_a_listed_version_passes_at_once(tmp_path):
    rc, out = _run_loop(tmp_path, [3, 1, 0])
    assert rc == 0 and "::warning::" not in out and "::error::" not in out, out[-400:]
