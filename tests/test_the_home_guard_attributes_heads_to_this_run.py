"""3.16.3: the run-end guard counts a chain head as this run's only when its store is under this run's own temp root.

Measured 2026-10-05: a -m mutation run failed the guard on 6 heads of temporary `s.json` stores, and each of the 4
mutation-marked files, run alone with a watcher polling the heads directory every 0.1 s, wrote none. The guard
attributed a head by whether its store sat under the SYSTEM temp directory, so another session's temp store
failed this run. The conftest now points TEMP, TMP and TMPDIR at a root it creates, and the guard counts a head
as a leak only when its store is under that root. A head it cannot read, or one that names no store, still fails:
what the guard cannot attribute is never excused.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _home_guard  # noqa: E402

HEADS = os.path.join("AppData", "Roaming", "inspeximus", "heads")


def _home(tmp_path):
    home = tmp_path / "realhome"
    (home / HEADS).mkdir(parents=True)
    return str(home)


def _head(home, name, store):
    with open(os.path.join(home, HEADS, name), "w", encoding="utf-8") as fh:
        if store is None:
            fh.write("{not json")
        else:
            json.dump({"path": store} if store else {"genesis": "x"}, fh)


def _classify(tmp_path, store):
    home = _home(tmp_path)
    run_root = tmp_path / "this-run"
    other = tmp_path / "another-process-temp"
    run_root.mkdir()
    other.mkdir()
    before = _home_guard.snapshot(home)
    _head(home, "h1.json", store(str(run_root), str(other)))
    after = _home_guard.snapshot(home)
    return _home_guard.classify(before, after, home, temp_roots=[str(run_root)])


def test_a_head_of_another_process_temp_store_is_reported_not_failed(tmp_path):
    fail, info = _classify(tmp_path, lambda run, other: os.path.join(other, "tmpabc", "s.json"))
    assert not fail, fail
    assert any("another process" in x for x in info), info


def test_a_head_of_a_store_under_this_runs_temp_root_fails(tmp_path):
    fail, info = _classify(tmp_path, lambda run, other: os.path.join(run, "tmpxyz", "s.json"))
    assert fail and any("new files in the real key home" in x for x in fail), (fail, info)


def test_a_head_the_guard_cannot_read_fails(tmp_path):
    fail, _ = _classify(tmp_path, lambda run, other: None)
    assert fail and any("unreadable" in x for x in fail), fail


def test_a_head_that_names_no_store_fails(tmp_path):
    fail, _ = _classify(tmp_path, lambda run, other: "")
    assert fail and any("unreadable" in x for x in fail), fail


def test_every_test_and_its_children_use_this_runs_temp_root():
    """The attribution above is only as good as this: TEMP points at the run's root in every worker."""
    root = os.environ.get("PYTEST_INSPEXIMUS_RUN_TMP")
    assert root and os.path.isdir(root), "the conftest did not create this run's temp root"
    r = os.path.normcase(os.path.realpath(root))
    for name in ("TEMP", "TMP", "TMPDIR"):      # a fixture may narrow it to a subdirectory; never outside
        v = os.environ.get(name) or ""
        assert os.path.normcase(os.path.realpath(v)).startswith(r), (name, v, root)
    assert os.path.normcase(os.path.realpath(tempfile.gettempdir())).startswith(r)
    assert os.path.normcase(os.path.realpath(tempfile.mkdtemp())).startswith(r)


# ── the whole key home, in both views (AUDIT-A, 2026-10-08) ─────────────────────────────────────────────────────
KEYS = os.path.join("AppData", "Roaming", "inspeximus", "keys")
PKG = os.path.join("AppData", "Local", "Packages", "PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0", "LocalCache",
                   "Roaming", "inspeximus")


def _new_file_fails(tmp_path, rel, name):
    home = _home(tmp_path)
    os.makedirs(os.path.join(home, rel), exist_ok=True)
    before = _home_guard.snapshot(home)
    with open(os.path.join(home, rel, name), "w", encoding="utf-8") as fh:
        fh.write("00" * 32)
    after = _home_guard.snapshot(home)
    return _home_guard.classify(before, after, home, temp_roots=[str(tmp_path / "this-run")])


def test_control_a_key_written_into_the_real_keys_folder_fails_the_run(tmp_path):
    """The float16 probe wrote keys here and the guard, which listed only heads, saw nothing."""
    fail, _ = _new_file_fails(tmp_path, KEYS, "6cf7868f990c36a8.guards.key")
    assert fail and any("6cf7868f990c36a8.guards.key" in x for x in fail), fail


def test_a_file_in_any_folder_of_the_key_home_fails_the_run(tmp_path):
    fail, _ = _new_file_fails(tmp_path, os.path.join("AppData", "Roaming", "inspeximus", "salts"), "x.salt")
    assert fail, fail


def test_a_key_in_the_store_pythons_package_copy_fails_the_run(tmp_path):
    """A Store Python redirects writes under APPDATA into its package folder; that view is listed as well."""
    fail, _ = _new_file_fails(tmp_path, os.path.join(PKG, "keys"), "abc.guards.key")
    assert fail and any("Packages" in x for x in fail), fail


def test_a_live_head_in_the_package_copy_is_reported_not_failed(tmp_path):
    home = _home(tmp_path)
    os.makedirs(os.path.join(home, PKG, "heads"))
    before = _home_guard.snapshot(home)
    with open(os.path.join(home, PKG, "heads", "h.json"), "w", encoding="utf-8") as fh:
        json.dump({"path": str(tmp_path / "elsewhere" / "s.json")}, fh)
    fail, info = _home_guard.classify(before, _home_guard.snapshot(home), home,
                                      temp_roots=[str(tmp_path / "this-run")])
    assert not fail and info, (fail, info)
