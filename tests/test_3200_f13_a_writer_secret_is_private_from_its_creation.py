"""3.20, F13 (Claude Security scan, LOW): `inspeximus writer-key --new --out` creates the secret 0600 and never overwrites.

THE DEFECT. The secret was written with `Path.write_text`, which creates the file with the process's default mode
(0644 under the usual umask), and `os.chmod(p, 0o600)` ran after it, best-effort. Between the two calls another
account on the machine could open the file and keep the handle. And an existing file at `--out` was overwritten:
a second run replaced a writer identity that records already carry, and a link a repository shipped at that name was
followed to the file it names.

WHAT IS PINNED HERE. The file is opened with O_CREAT | O_EXCL and mode 0600 in the one call that creates it, so it is
never readable by another account and an existing name, a link included, is refused with exit 2 and left untouched.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import cli  # noqa: E402


def test_the_secret_is_created_0600_and_exclusive_in_one_call(tmp_path, monkeypatch):
    out = str(tmp_path / "key.txt")
    seen = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        if os.fspath(path) == out:
            seen.append((flags, mode))
        return real_open(path, flags, mode, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    assert cli.main(["writer-key", "--new", "--out", out]) == 0
    assert len(seen) == 1, "the secret was not created through one os.open: %r" % seen
    flags, mode = seen[0]
    assert flags & os.O_CREAT and flags & os.O_EXCL, "the secret is not created exclusively"
    assert mode == 0o600, "the secret is created with mode %o" % mode
    assert open(out, encoding="utf-8").read().strip(), "CONTROL: a secret was written"


def test_an_existing_file_is_refused_and_left_untouched(tmp_path, capsys):
    out = tmp_path / "key.txt"
    out.write_text("the identity my records carry\n", encoding="utf-8")
    assert cli.main(["writer-key", "--new", "--out", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "the identity my records carry\n"
    assert "already exists" in capsys.readouterr().err


def test_a_link_at_the_name_is_refused_and_its_target_untouched(tmp_path):
    target = tmp_path / "users_file.txt"
    target.write_text("mine\n", encoding="utf-8")
    link = tmp_path / "key.txt"
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create a symbolic link")
    assert cli.main(["writer-key", "--new", "--out", str(link)]) == 2
    assert target.read_text(encoding="utf-8") == "mine\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_on_posix_the_file_is_0600_even_under_an_open_umask(tmp_path):
    old = os.umask(0)
    try:
        out = tmp_path / "key.txt"
        assert cli.main(["writer-key", "--new", "--out", str(out)]) == 0
    finally:
        os.umask(old)
    assert (os.stat(out).st_mode & 0o777) == 0o600
