"""The coordinator reads the target's files itself, so an adapter cannot pass by reporting a zero.

Found on 2026-09-22 in a joint probe run with MemStrata, whose lifecycle coordinator had the identical
shape: `erase()` and `still_recoverable()` are both the target's methods, so a target that deletes
nothing and reports nothing recoverable was recorded as verified. The verification was performed by the
thing being verified.

The control that matters is `test_a_target_that_names_no_file_is_labelled_rather_than_trusted`: the fix
must not report a scan it never did. A store that is not file-backed still gets an entry, and that entry
says in the record that the target vouched for itself.
"""
from __future__ import annotations

import json

from inspeximus.deletion_manifest import DeletionManifest, ErasureTarget

MARKER = "alice@example.com"


class _FileTarget(ErasureTarget):
    """A file-backed store. `honest` decides whether erase() really removes the marker."""

    def __init__(self, path, honest=True, name="file-target", claim_clean=True, codec="utf-8"):
        self.name, self._path, self._honest = name, path, honest
        self._claim_clean, self._codec = claim_clean, codec
        path.write_bytes(("id=1 " + MARKER + "\nid=2 bob@example.com\n").encode(codec))

    def erase(self, subject):
        if self._honest:
            self._path.write_bytes("id=2 bob@example.com\n".encode(self._codec))
            return {"erased": 1}
        return {"erased": 1}                                   # says it deleted, and did not

    def still_recoverable(self, subject, values):
        return not self._claim_clean                           # the target's own claim

    def files(self):
        return [str(self._path)]


class _OpaqueTarget(ErasureTarget):
    """A store with no files to read: an API the manifest cannot inspect."""

    name = "opaque-target"

    def erase(self, subject):
        return {"erased": 1}

    def still_recoverable(self, subject, values):
        return False


def _run(tmp_path, target):
    m = DeletionManifest().register(target)
    return m, m.execute("alice", [MARKER], request_id="r1", basis="gdpr-17", authorized_by="dpo")


def test_a_lying_target_is_caught_by_the_manifests_own_read(tmp_path):
    man = _run(tmp_path, _FileTarget(tmp_path / "store.txt", honest=False))[1]
    entry = man["entries"][0]
    assert entry["independent_scan"]["matches"] == 1
    assert entry["still_recoverable"] is True
    assert entry["verified_absent"] is False
    assert man["complete"] is False and man["residual_targets"] == ["file-target"]


def test_an_honest_target_still_verifies(tmp_path):
    m, man = _run(tmp_path, _FileTarget(tmp_path / "store.txt", honest=True))
    entry = man["entries"][0]
    assert entry["independent_scan"]["matches"] == 0
    assert entry["independent_scan"]["files_read"] == 1
    assert man["complete"] is True and man["residual_targets"] == []
    assert m.verify(man) == (True, [])


def test_a_utf16_store_is_read_in_its_own_encoding(tmp_path):
    """A scan that only knows UTF-8 reports a UTF-16 store clean, which is the same defect one layer in."""
    man = _run(tmp_path, _FileTarget(tmp_path / "s16.txt", honest=False, codec="utf-16-le"))[1]
    assert man["entries"][0]["independent_scan"]["matches"] == 1
    assert man["complete"] is False


def test_a_target_that_names_no_file_is_labelled_rather_than_trusted(tmp_path):
    m, man = _run(tmp_path, _OpaqueTarget())
    entry = man["entries"][0]
    assert entry["independent_scan"] is None                   # no scan is reported, because none ran
    assert entry["verified_absent"] is True                    # the target's claim still stands
    assert m.verify(man) == (True, [])


class _MissingFileTarget(_OpaqueTarget):
    """A target that names a file which is not there: a backup rotated away, a path typo, a mount gone."""

    name = "missing-file-target"

    def __init__(self, path):
        self._path = path

    def files(self):
        return [str(self._path)]


def test_an_unreadable_file_is_coverage_we_do_not_have(tmp_path):
    man = _run(tmp_path, _MissingFileTarget(tmp_path / "never-written.txt"))[1]
    scan = man["entries"][0]["independent_scan"]
    assert scan["files_read"] == 0 and scan["unreadable"] and scan["matches"] == 0


def test_the_scan_is_inside_the_hash_chain(tmp_path):
    m, man = _run(tmp_path, _FileTarget(tmp_path / "store.txt", honest=True))
    assert m.verify(man) == (True, [])
    tampered = json.loads(json.dumps(man))
    tampered["entries"][0]["independent_scan"]["matches"] = 7
    ok, problems = m.verify(tampered)
    assert ok is False and any("hash" in p or "chain" in p for p in problems)


def test_a_manifest_written_before_the_scan_existed_still_verifies(tmp_path):
    """The control for the fix itself: adding a field to the hash must not break older evidence."""
    m, man = _run(tmp_path, _FileTarget(tmp_path / "store.txt", honest=True))
    legacy = json.loads(json.dumps(man))
    for e in legacy["entries"]:
        core = {k: e[k] for k in ("target", "erased", "still_recoverable", "verified_absent",
                                  "error", "ts", "prev")}
        from inspeximus.deletion_manifest import _sha256
        e.pop("independent_scan")
        e["hash"] = _sha256(core)
    legacy["chain_tip"] = legacy["entries"][-1]["hash"]
    assert DeletionManifest().verify(legacy) == (True, [])
