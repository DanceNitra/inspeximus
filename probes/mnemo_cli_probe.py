"""mnemo_cli_probe.py — the `mnemo` shell CLI (remember/recall/revert/forget/list/stats).

Drives mnemo.cli.main(argv) against a temp store and asserts the correction lifecycle works from the command
line: a keyed re-write supersedes (recall shows current-truth), revert rolls back, forget hard-deletes."""
import sys, pathlib, tempfile, os, io, contextlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import cli


def _run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(list(argv))
    return buf.getvalue()


def run():
    ok = {}
    tmp = os.path.join(tempfile.mkdtemp(), "cli.json")
    P = ["--path", tmp]

    _run(*P, "remember", "the deploy channel is BLUE-9", "--key", "deploy-channel")
    _run(*P, "remember", "the deploy channel is RED-2", "--key", "deploy-channel")
    out = _run(*P, "recall", "what is the deploy channel")
    ok["A keyed re-write -> current-truth (RED-2, not BLUE-9)"] = ("RED-2" in out and "BLUE-9" not in out)

    _run(*P, "revert", "deploy-channel")
    out = _run(*P, "recall", "what is the deploy channel")
    ok["B revert -> predecessor restored (BLUE-9)"] = ("BLUE-9" in out and "RED-2" not in out)

    out = _run(*P, "stats")
    ok["C stats reports the store"] = ("total" in out and "keyed" in out)

    out = _run(*P, "list", "-n", "5")
    ok["D list shows active memory"] = ("deploy channel" in out)

    _run(*P, "forget", "--key", "deploy-channel")
    out = _run(*P, "recall", "what is the deploy channel")
    ok["E forget hard-deletes"] = ("BLUE-9" not in out and "RED-2" not in out)

    out = _run(*P, "--json", "stats")
    ok["F --json emits JSON"] = out.strip().startswith("{")

    print("=" * 58)
    print("mnemo CLI — remember/recall/revert/forget/list/stats")
    print("=" * 58)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 58)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(run())
