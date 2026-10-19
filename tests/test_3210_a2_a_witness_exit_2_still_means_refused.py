"""A witness exit code 2 still means "refused", never "you mistyped a path" (3.21, AUDIT-A A-2).

`inspeximus witness` documents its exit codes as the contract a CI job or a cron auditor reads: 1 = the check failed,
2 = the witness REFUSED to co-sign (a fork or a rollback). F-2 made every handled input mistake exit 2, so on a09dc529
a missing anchor file, an anchor that is not JSON and a missing key file exited 2 in `witness cosign`, and a script
that raises a fork alarm on 2 raised it for a typo. A handled input mistake in a witness command now exits 1, and a
real rollback still exits 2. `timestamp qualified` keeps 2 for an unreadable file: its 1 is the verdict "not
qualified", and 2 is its "undetermined, stop".
"""
import contextlib
import io
import json

from inspeximus import cli
from inspeximus.core import new_ed25519_keypair, sth_hash_of


def _run(argv):
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
    except SystemExit as e:
        return e.code, err.getvalue(), None
    except BaseException as e:                                           # noqa: BLE001
        return None, err.getvalue(), e
    return rc, err.getvalue(), None


def _head(n):
    a = {"n_writes": n, "n_tombstones": 0, "writes_tip": "%064x" % n, "tombstones_tip": "00" * 32}
    a["sth_hash"] = sth_hash_of(a)
    return a


def _witness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    sk, _pk = new_ed25519_keypair()
    (tmp_path / "w.key").write_text(sk)
    for n in (5, 3):
        (tmp_path / ("h%d.json" % n)).write_text(json.dumps(_head(n)))
    (tmp_path / "bad.json").write_text("{not json")
    return ["witness", "cosign", "--store-id", "s1", "--key", "w.key", "--state", "w.state.json"]


def test_a_rollback_is_still_refused_with_exit_2(tmp_path, monkeypatch):
    base = _witness(tmp_path, monkeypatch)
    rc, _err, exc = _run(base + ["h5.json"])
    assert exc is None and rc == 0, "CONTROL: the first head is co-signed (%r, %r)" % (rc, exc)
    rc, err, exc = _run(base + ["h3.json"])
    assert exc is None and rc == 2, (rc, exc)
    assert "REFUSED" in err, err


def test_a_witness_input_mistake_exits_1_without_a_traceback(tmp_path, monkeypatch):
    base = _witness(tmp_path, monkeypatch)
    cases = {"a missing anchor file": base + ["nope.json"],
             "an anchor that is not JSON": base + ["bad.json"],
             "a missing key file": ["witness", "cosign", "--store-id", "s1", "--key", "nokey", "h5.json"]}
    for what, argv in cases.items():
        rc, err, exc = _run(argv)
        assert exc is None, "%s: %r" % (what, exc)
        assert rc == 1, "%s exited %r, which is the witness's REFUSED code" % (what, rc)
        assert err.startswith("inspeximus witness cosign: error: ") and err.count("\n") == 1, (what, err)


def test_timestamp_qualified_keeps_undetermined_for_an_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    (tmp_path / "tl.json").write_text("{}")
    rc, err, exc = _run(["timestamp", "qualified", "missing.tsr", "--trusted-list", "tl.json"])
    assert exc is None, repr(exc)
    assert rc == 2, "an unreadable token must not read as 1, the verdict 'not qualified' (%r)" % rc
    assert err.startswith("inspeximus timestamp qualified: error: "), err


def test_other_commands_still_exit_2_for_an_input_mistake(tmp_path, monkeypatch):
    """CONTROL: the exception is narrow. writer-key, which defines no 2 of its own, keeps argparse's code."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_TRACEBACK", raising=False)
    rc, _err, exc = _run(["writer-key", "--new", "--out", str(tmp_path / "no" / "such" / "k.txt")])
    assert exc is None and rc == 2, (rc, exc)
