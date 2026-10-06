"""AUDIT-A round 2 on v3.16.3: a DIRECTORY where the store file should be makes every hook wait 8 to 10 seconds.
`_open_store_bytes` retries PermissionError 40 times at 0.2 s, which is right for a sharing violation on a file and can
never succeed for a directory. A repository that ships `.inspeximus/coding_memory.json/<any file>` (git stores the
file, so the directory appears on checkout) costs the user, measured: SessionStart 10.0 s, UserPromptSubmit 8.9 s,
PostToolUse 8.7 s per call, each ending in "hook ... failed: PermissionError". INSPEXIMUS_DECISION_STORE pointing at a
directory costs 8.2 s per prompt the same way. Fails on v3.16.3 (about 8 s)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.core import Inspeximus  # noqa: E402


def test_a_directory_at_the_store_path_is_refused_at_once(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    p = tmp_path / ".inspeximus" / "coding_memory.json"
    (p / "sub").mkdir(parents=True)
    (p / "sub" / "f.txt").write_text("x")
    t0 = time.time()
    try:
        Inspeximus(str(p))
    except Exception:
        pass
    took = time.time() - t0
    assert took < 2.0, f"opening a directory as a store took {took:.1f} s"
