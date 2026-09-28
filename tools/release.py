"""The ONLY way to tag an inspeximus release: push HEAD to main, wait for CI on that exact commit, then tag.

    python tools/release.py X.Y.Z --dry-run         # the pre-flight only; nothing leaves this machine
    python tools/release.py X.Y.Z                   # push, wait for CI, tag, follow the release run
    python tools/release.py X.Y.Z --approve-pypi    # also approve the `pypi` environment (the owner's yes)

WHY THIS EXISTS. On 2026-09-28 v3.15.0 was tagged by hand before CI had run on its commit. The release
workflow's test gate then failed on a stale mutation target, and the tag cannot be deleted (repository
rule GH013), so 3.15.0 is a version that will never exist and 3.15.1 shipped the fixed tree. The guard
that would have stopped it lived in one session's scratch script; a guard only one session has protects
one session. Every guard below refuses BEFORE the step it protects, and says which one fired.

Pre-flight, before anything leaves this machine:
  1. `tools/release_check.py --skip-tests` reports no [FAIL]. "ci on HEAD" is exempt: it cannot pass
     before the push, and guard 5 is its real check.
  2. pyproject.toml says X.Y.Z.
  3. vX.Y.Z exists neither locally nor on origin.
  4. HEAD is a fast-forward of origin/main, so the push cannot rewrite anything.
Then HEAD is pushed to main, and before the tag:
  5. the `push` run of ci.yml for exactly HEAD finished with conclusion success, and its headSha is HEAD.
  6. origin/main is still HEAD, so the tag names the commit CI just cleared.
Then the tag is created and pushed, the release run is followed, the `pypi` environment is approved only
with --approve-pypi, and the script waits until PyPI serves X.Y.Z.

Every external call goes through one `sh(args) -> (returncode, stdout)`, so the tests hand in a fake and
watch each guard fire without a network or a repository (tests/test_the_release_script_refuses_before_each_step.py).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = "DanceNitra/inspeximus"
#: The id of the repository's `pypi` deployment environment, whose approval is the owner's yes.
PYPI_ENVIRONMENT_ID = "18463543886"


def default_sh(args, cwd=None):
    r = subprocess.run(args, cwd=str(cwd or ROOT), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def pypi_version(version, timeout=30):
    """The version PyPI serves at /pypi/inspeximus/<version>/json, or None."""
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/inspeximus/%s/json" % version, timeout=timeout) as r:
            return json.load(r)["info"]["version"]
    except Exception:                                        # noqa: BLE001 - not there yet
        return None


class Stop(Exception):
    """A guard fired. The message names it."""


def preflight(version, sh, root=ROOT):
    """Guards 1-4. Returns the list of reasons to stop; empty means the push may go ahead."""
    reasons = []
    _, out = sh([sys.executable, "tools/release_check.py", "--skip-tests"])
    fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln and "ci on HEAD" not in ln]
    if fails:
        reasons.append("release_check reports a failure: " + " | ".join(fails))
    m = re.search(r'^version\s*=\s*"([^"]+)"', (pathlib.Path(root) / "pyproject.toml").read_text(encoding="utf-8"),
                  re.M)
    if not m or m.group(1) != version:
        reasons.append("pyproject.toml says %s, not %s" % (m.group(1) if m else "no version", version))
    if sh(["git", "rev-parse", "-q", "--verify", "refs/tags/v%s" % version])[0] == 0:
        reasons.append("tag v%s already exists here" % version)
    if sh(["git", "ls-remote", "--tags", "origin", "refs/tags/v%s" % version])[1].strip():
        reasons.append("tag v%s already exists on origin" % version)
    sh(["git", "fetch", "-q", "origin"])
    if sh(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"])[0] != 0:
        reasons.append("HEAD is not a fast-forward of origin/main")
    return reasons


def ci_run(head, sh, sleep, polls=180, every=30):
    """The finished `push` run of ci.yml for exactly `head`, or Stop."""
    for _ in range(polls):
        rc, out = sh(["gh", "run", "list", "-R", REPO, "--commit", head, "--workflow", "ci.yml",
                      "--json", "databaseId,event,headSha,status,conclusion"])
        runs = [r for r in (json.loads(out) if rc == 0 and out.strip().startswith("[") else [])
                if r.get("event") == "push" and r.get("headSha") == head]
        if runs and runs[0].get("status") == "completed":
            return runs[0]
        sleep(every)
    raise Stop("no finished push run of ci.yml for %s" % head)


def main(argv=None, sh=default_sh, sleep=time.sleep, pypi=pypi_version, out=print, root=ROOT):
    ap = argparse.ArgumentParser(prog="release.py", description="Push, wait for CI, then tag. The only way to tag.")
    ap.add_argument("version")
    ap.add_argument("--dry-run", action="store_true", help="run the pre-flight only")
    ap.add_argument("--approve-pypi", action="store_true", help="approve the pypi environment (the owner's yes)")
    a = ap.parse_args(argv)
    v = a.version
    try:
        reasons = preflight(v, sh, root)
        if reasons:
            raise Stop("; ".join(reasons))
        head = sh(["git", "rev-parse", "HEAD"])[1].strip()
        if a.dry_run:
            out("pre-flight clear for v%s at %s; --dry-run, nothing pushed" % (v, head[:12]))
            return 0
        rc, msg = sh(["git", "push", "origin", "%s:main" % head])
        if rc != 0:
            raise Stop("push to main refused: " + msg.strip()[-300:])
        out("pushed %s to main" % head[:12])
        run = ci_run(head, sh, sleep)
        if run.get("conclusion") != "success" or run.get("headSha") != head:
            raise Stop("main CI run %s is %s on %s, not success on %s" % (
                run.get("databaseId"), run.get("conclusion"), str(run.get("headSha"))[:12], head[:12]))
        out("main CI run %s: success on %s" % (run.get("databaseId"), head[:12]))
        now = sh(["git", "ls-remote", "origin", "refs/heads/main"])[1].split()
        if not now or now[0] != head:
            raise Stop("origin/main moved to %s after the push; the tag would not name what CI cleared"
                       % (now[0][:12] if now else "nothing"))
        for cmd in (["git", "tag", "-a", "v%s" % v, head, "-m", "inspeximus %s" % v],
                    ["git", "push", "origin", "v%s" % v]):
            rc, msg = sh(cmd)
            if rc != 0:
                raise Stop("%s failed: %s" % (" ".join(cmd[:3]), msg.strip()[-300:]))
        out("tagged v%s at %s" % (v, head[:12]))
        release_id = None
        for _ in range(60):
            rc, o = sh(["gh", "run", "list", "-R", REPO, "--workflow", "release.yml",
                        "--json", "databaseId,headBranch"])
            ids = [r["databaseId"] for r in (json.loads(o) if rc == 0 and o.strip().startswith("[") else [])
                   if r.get("headBranch") == "v%s" % v]
            if ids:
                release_id = ids[0]
                break
            sleep(10)
        if release_id is None:
            raise Stop("no release run for v%s" % v)
        out("release run %s" % release_id)
        if a.approve_pypi:
            for _ in range(120):
                o = sh(["gh", "api", "repos/%s/actions/runs/%s/pending_deployments" % (REPO, release_id)])[1]
                if o.strip().startswith("[") and json.loads(o):
                    sh(["gh", "api", "-X", "POST", "repos/%s/actions/runs/%s/pending_deployments" % (REPO, release_id),
                        "-F", "environment_ids[]=%s" % PYPI_ENVIRONMENT_ID, "-f", "state=approved",
                        "-f", "comment=owner approved %s" % v])
                    out("approved the pypi environment")
                    break
                sleep(30)
        else:
            out("waiting for the owner to approve the pypi environment of run %s" % release_id)
        for _ in range(120):
            if pypi(v) == v:
                out("PyPI serves %s" % v)
                return 0
            sleep(30)
        raise Stop("PyPI does not serve %s yet; check release run %s" % (v, release_id))
    except Stop as e:
        out("STOP: %s" % e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
