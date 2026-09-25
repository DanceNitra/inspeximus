"""release_check's fast phase selects the tests that read a changed non-Python file.

`fast_selection` picked tests by the names of changed `.py` modules. A release whose only change was
CHANGELOG.md, a doc, or a version carrier such as .mcp.json selected no test at all, so the fast phase
passed on nothing and the first signal came from the full suite half an hour later (handoff
2026-09-25, item 5). A test that names the changed file is the one nearest the change.

Control: a changed file no test names still selects nothing, so the selection is by name and not
"everything".
"""
import os
import pathlib
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import release_check  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "CHANGELOG.md").write_text("## 1.0.0\n", encoding="utf-8")
    (repo / "docs" / "API.md").write_text("api\n", encoding="utf-8")
    (repo / "NOTES.txt").write_text("notes\n", encoding="utf-8")
    (repo / "tests" / "test_changelog.py").write_text(
        'P = "CHANGELOG.md"\ndef test_x():\n    pass\n', encoding="utf-8")
    (repo / "tests" / "test_api_doc.py").write_text(
        'P = ROOT / "docs" / "API.md"\ndef test_x():\n    pass\n', encoding="utf-8")
    (repo / "tests" / "test_other.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "tag", "v1.0.0")
    return repo


def test_a_changelog_only_change_selects_the_tests_that_read_it(tmp_path):
    repo = _repo(tmp_path)
    (repo / "CHANGELOG.md").write_text("## 1.0.1\n\n## 1.0.0\n", encoding="utf-8")
    sel, tag, _ = release_check.fast_selection(pathlib.Path(repo))
    assert tag == "v1.0.0", "control: the diff is taken against the last release tag"
    assert sel == ["tests/test_changelog.py"], sel


def test_a_doc_change_selects_the_tests_that_read_it(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs" / "API.md").write_text("api, edited\n", encoding="utf-8")
    sel, _, _ = release_check.fast_selection(pathlib.Path(repo))
    assert sel == ["tests/test_api_doc.py"], sel


def test_a_file_no_test_names_selects_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "NOTES.txt").write_text("notes, edited\n", encoding="utf-8")
    sel, _, _ = release_check.fast_selection(pathlib.Path(repo))
    assert sel == [], sel
