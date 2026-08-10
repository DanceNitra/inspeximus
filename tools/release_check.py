"""The pre-release checklist, as code that returns an exit status.

RELEASING.md carries the procedure, and three times now the same class of defect has got past it:
a step that only a human remembers is a step that gets skipped.

  * 1.86.0 shipped with CI red because two registry manifests were stale -- the pinner existed, but it
    lived in the release workflow and nowhere in the human procedure.
  * 1.89.0 shipped `inspeximus/core.py` at the previous version for the same reason, one layer in.
  * `CITATION.cff` -- the file Zenodo and every citation of this software read -- said `1.1.0` while
    the package went from 1.2.0 to 1.88.1. Measured over the git history: **111 distinct released
    versions disagreed with it.** Nothing pinned it and no test asserted it, so nobody found out.

Each of those was discoverable by reading four files and comparing five strings. This does that, plus
the checks whose absence would let a release ship something that does not run, and it exits non-zero
when any of them fails.

Exit codes -- an unrun check is not a passing check:

    0   every check ran and passed
    1   at least one check FAILED
    2   nothing failed, but at least one check was SKIPPED (its precondition was absent, or you
        passed --skip-tests). The release is not cleared.

Run it:  python tools/release_check.py [--skip-tests]
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Files that carry the package version and MUST agree with pyproject.toml.
#
# `probes/*.json` is deliberately NOT here. Those records name the version a measurement RAN under;
# pinning them would rewrite the provenance of a number to make a checklist happy, which is the
# opposite of what this file is for. If a probe result disagrees with pyproject, that is correct.
REQUIRED_CARRIERS = (
    "pyproject.toml",
    "inspeximus/core.py",
    "CITATION.cff",
    "server.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "README.md",
    "glama.json",
)


class Report:
    """Every check runs; nothing short-circuits. A run that stops at the first failure hides the rest."""

    def __init__(self):
        self.rows = []

    def add(self, name, status, detail):
        self.rows.append((name, status, detail))
        print("  [%s] %-26s %s" % (status, name, detail), flush=True)

    def exit_code(self):
        if any(s == FAIL for _, s, _ in self.rows):
            return 1
        if any(s == SKIP for _, s, _ in self.rows):
            return 2
        return 0


# --------------------------------------------------------------------------- version carriers

def pyproject_version(root=ROOT):
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("::error::pyproject.toml has no version; the source of truth is unreadable")
    return m.group(1)


def _read_carrier(root, rel):
    """-> (list[(label, value)], error_or_None).

    An empty list with no error is a reader that found nothing, which is NOT the same as agreement --
    the caller treats it as a failure for every required carrier except the one documented below.
    """
    path = root / rel
    if not path.exists():
        return [], "file is missing"
    try:
        if rel == "pyproject.toml":
            m = re.search(r'^version = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
            return ([("version", m.group(1))] if m else []), None
        if rel == "inspeximus/core.py":
            found = re.findall(r'^__version__ = "([^"]+)"$', path.read_text(encoding="utf-8"), re.M)
            if len(found) > 1:
                return [], "more than one __version__ assignment; which one ships is ambiguous"
            return [("__version__", v) for v in found], None
        if rel == "CITATION.cff":
            # CFF is YAML; the version may be bare or quoted. Parsed with a narrow regex on purpose --
            # this tool has zero dependencies, exactly like the library it releases.
            m = re.search(r'^version:\s*["\']?([^"\'\s]+)["\']?\s*$',
                          path.read_text(encoding="utf-8"), re.M)
            return ([("version", m.group(1))] if m else []), None
        if rel == "server.json":
            d = json.loads(path.read_text(encoding="utf-8"))
            out = [("version", d["version"])] if "version" in d else []
            for i, pkg in enumerate(d.get("packages", [])):
                if "version" in pkg:
                    out.append(("packages[%d].version" % i, pkg["version"]))
            return out, None
        if rel == ".claude-plugin/plugin.json":
            d = json.loads(path.read_text(encoding="utf-8"))
            return ([("version", d["version"])] if "version" in d else []), None
        if rel == ".claude-plugin/marketplace.json":
            # Only the per-plugin entries are the package version. The top-level one is the
            # marketplace SCHEMA's own version (1.0.0) and pinning it would corrupt the manifest.
            d = json.loads(path.read_text(encoding="utf-8"))
            return [("plugins[%d].version" % i, e["version"])
                    for i, e in enumerate(d.get("plugins", [])) if "version" in e], None
        if rel == "README.md":
            toks = re.findall(r'(?<![\w.])v(\d+\.\d+\.\d+)(?![\w.])', path.read_text(encoding="utf-8"))
            return [("badge[%d]" % i, v) for i, v in enumerate(toks)], None
        if rel == "glama.json":
            # It declares no version today. That is fine and it is REPORTED rather than passed over in
            # silence: a check that reads an absent field and says nothing has measured nothing. The
            # file's existence is still required -- if the listing disappears we want to hear about it.
            d = json.loads(path.read_text(encoding="utf-8"))
            return ([("version", d["version"])] if "version" in d else []), None
    except Exception as exc:                                     # noqa: BLE001 - report, never crash
        return [], "unreadable: %s" % exc
    return [], "no reader is defined for this carrier"


def check_version_carriers(rep, root=ROOT):
    want = pyproject_version(root)
    bad, empty, missing, silent, checked = [], [], [], [], 0
    for rel in REQUIRED_CARRIERS:
        found, err = _read_carrier(root, rel)
        if err:
            missing.append("%s (%s)" % (rel, err))
            continue
        if not found:
            # glama.json is the one carrier allowed to declare no version -- see its reader. Every
            # other one reading empty is a reader that stopped matching, which must be loud: a
            # version check that silently examines nothing is the failure this file exists to stop.
            if rel != "glama.json":
                empty.append(rel)
            else:
                silent.append(rel)
            continue
        for label, value in found:
            checked += 1
            if value != want:
                bad.append("%s:%s = %s" % (rel, label, value))

    if missing or empty or bad:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if empty:
            parts.append("declares no version: " + ", ".join(empty))
        if bad:
            parts.append("disagrees with pyproject %s -> %s" % (want, "; ".join(bad)))
        rep.add("version carriers", FAIL, " | ".join(parts))
        return
    note = (" (%s declares none, by design)" % ", ".join(silent)) if silent else ""
    rep.add("version carriers", PASS,
            "%d version fields across %d files all say %s%s"
            % (checked, len(REQUIRED_CARRIERS) - len(silent), want, note))


def check_import_version(rep, root=ROOT):
    """The version a USER reads, from the tree that is about to be released.

    Asserts WHICH inspeximus answered. An installed copy elsewhere on sys.path would otherwise report a
    version that has nothing to do with this checkout, and the check would pass while measuring another
    tree entirely.
    """
    code = "import json, inspeximus; print(json.dumps([inspeximus.__version__, inspeximus.__file__]))"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                          capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
        rep.add("import inspeximus", FAIL, "import failed: %s" % last)
        return
    version, where = json.loads(proc.stdout.strip())
    # Ancestry, not a fixed number of `.parent` hops: the latter silently answers the wrong question
    # if the package layout ever gains or loses a level, and it is comparing two paths that were not
    # resolved the same way. `root` must be an ancestor of the file that answered.
    if root.resolve() not in pathlib.Path(where).resolve().parents:
        rep.add("import inspeximus", FAIL,
                "imported %s, which is not this tree -- the check would measure another checkout" % where)
        return
    if version != pyproject_version(root):
        rep.add("import inspeximus", FAIL,
                "inspeximus.__version__ = %s, pyproject says %s" % (version, pyproject_version(root)))
        return
    rep.add("import inspeximus", PASS, "__version__ = %s, from this tree" % version)


# --------------------------------------------------------------------------- changelog

VERSION_HEADING = re.compile(r'^## (\d+\.\d+\.\d+)\b', re.M)


def changelog_entry(root, version):
    """The body of the `## <version>` section, or None. Headings that do not start with a version --
    `## Audit notes from the day 1.87.0 was assembled` -- are not entries and are skipped."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    starts = [(m.start(), m.group(1)) for m in VERSION_HEADING.finditer(text)]
    for i, (pos, ver) in enumerate(starts):
        if ver == version:
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            return text[pos:end].rstrip()
    return None


def check_changelog(rep, root=ROOT):
    version = pyproject_version(root)
    entry = changelog_entry(root, version)
    if entry is None:
        rep.add("changelog entry", FAIL, "CHANGELOG.md has no `## %s` entry" % version)
        return
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = VERSION_HEADING.search(text)
    if newest.group(1) != version:
        rep.add("changelog entry", FAIL,
                "the newest entry is %s, but this release is %s -- newest goes on top"
                % (newest.group(1), version))
        return
    body = entry.split("\n", 1)[1].strip() if "\n" in entry else ""
    if len(body) < 80:
        rep.add("changelog entry", FAIL,
                "the %s entry has a heading and %d characters of body; a reader cannot act on that"
                % (version, len(body)))
        return
    rep.add("changelog entry", PASS,
            "`## %s` is the newest entry, %d characters" % (version, len(body)))


# --------------------------------------------------------------------------- zero dependencies

# Blocks by WHERE a module lives, not by an allowlist of stdlib names.
#
# The first version used `sys.stdlib_module_names`, which arrived in 3.10 -- and `pyproject.toml`
# declares `requires-python = ">=3.8"` with CI running 3.9. So on the OLDEST SUPPORTED PYTHON the
# blocker could not be built at all, the runtime leg degraded to a SKIP, and the guard covering the
# claim on the first line of the README was silently absent exactly where the package is most fragile.
# CI on 3.9 is what caught it; every local run here is 3.12.
#
# "Third-party" is really a question about location, and `site-packages` / `dist-packages` / `.egg`
# answers it on every version with no table to keep current. A stdlib module resolves inside the
# stdlib directory and passes; anything pip installed does not.
_BLOCKER = r'''
import sys, importlib.machinery
ALLOW = {"inspeximus", "__main__"}
MARKERS = ("site-packages", "dist-packages", ".egg")

class Blocker:
    def find_module(self, name, path=None):      # py<3.12 compatibility shim, harmless on 3.12+
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if "." in name:                          # a submodule of a package already vetted below
            return None
        if name in ALLOW:
            return None
        try:
            spec = importlib.machinery.PathFinder.find_spec(name)
        except Exception:
            return None
        if spec is None:                         # builtin/frozen, or genuinely absent
            return None
        where = (getattr(spec, "origin", "") or "") + "|" + \
                "|".join(list(getattr(spec, "submodule_search_locations", None) or []))
        if any(m in where for m in MARKERS):
            raise ImportError("THIRD_PARTY_BLOCKED:" + name)
        return None

sys.meta_path.insert(0, Blocker())
'''


def _blocked_run(root, body):
    code = _BLOCKER + "\n" + body
    return subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True, text=True, errors="replace")


def check_zero_dependencies(rep, root=ROOT):
    """Three legs, because any one alone can pass while the claim is false.

    1. DECLARED: `[project] dependencies` in pyproject must be empty. This is what an installer obeys.
    2. RUNTIME: `import inspeximus` and use it with every installed-package import blocked. A
       declaration can be empty while the code imports something that happens to be present in the dev
       environment.
    3. THE CONTROL: the blocker must actually block. It is aimed at an importable third-party module
       that is verified to import NORMALLY first -- otherwise "nothing was blocked" and "nothing was
       importable" produce the same green.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
    declared = re.findall(r'"([^"]+)"', block.group(1)) if block else []
    if declared:
        rep.add("zero dependencies", FAIL,
                "pyproject declares required dependencies: %s" % declared)
        return

    proc = _blocked_run(root, 'from inspeximus import Inspeximus\n'
                              'm = Inspeximus()\n'
                              'm.remember("the release checklist is a script")\n'
                              'print("OK", len(m.recall("release checklist", k=1)))\n')
    if proc.returncode != 0 or not proc.stdout.startswith("OK"):
        last = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
        rep.add("zero dependencies", FAIL,
                "import+use under a third-party import block failed: %s" % last)
        return

    control = None
    for cand in ("pytest", "yaml", "cryptography", "mcp", "numpy", "setuptools"):
        normal = subprocess.run([sys.executable, "-c", "import %s" % cand], cwd=str(root),
                                capture_output=True, text=True, errors="replace")
        if normal.returncode == 0:
            control = cand
            break
    if control is None:
        rep.add("zero dependencies", SKIP,
                "no third-party module is importable here, so the blocker could not be shown to block")
        return
    blocked = _blocked_run(root, "import %s" % control)
    if blocked.returncode == 0 or "THIRD_PARTY_BLOCKED" not in blocked.stderr:
        rep.add("zero dependencies", FAIL,
                "the import blocker let %s through, so the runtime leg above proves nothing" % control)
        return
    rep.add("zero dependencies", PASS,
            "0 declared; remember+recall ran with non-stdlib imports blocked (control: %s was blocked)"
            % control)


def check_mcp_server(rep, root=ROOT):
    """The MCP server is how most people reach this package, and it is the one part with a dependency.

    An absent `mcp` is a SKIP, never a PASS: not being able to look is not the same as having looked.
    """
    have = subprocess.run([sys.executable, "-c", "import mcp"], cwd=str(root),
                          capture_output=True, text=True, errors="replace")
    if have.returncode != 0:
        rep.add("mcp server imports", SKIP,
                "the `mcp` extra is not installed here; install `.[mcp]` to clear this check")
        return
    code = ("import inspeximus.mcp_server as s, re, inspect;"
            "print(len(re.findall(r'^@mcp\\.tool\\(\\)', inspect.getsource(s), re.M)))")
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
        rep.add("mcp server imports", FAIL, "import inspeximus.mcp_server failed: %s" % last)
        return
    rep.add("mcp server imports", PASS, "imports clean, %s tools registered" % proc.stdout.strip())


# --------------------------------------------------------------------------- the release notes

def check_release_notes(rep, root=ROOT):
    """The notes must BUILD for this version, and the example in them must actually run.

    A "try it in 30 seconds" block that no longer works is worse than none -- the release is when new
    readers arrive (see RELEASING.md for the download figure and its provenance), so the example is the
    first code most of them run.
    """
    notes = root / "tools" / "release_notes.py"
    if not notes.exists():
        rep.add("release notes", FAIL, "tools/release_notes.py is missing")
        return
    proc = subprocess.run([sys.executable, str(notes), "--check"], cwd=str(root),
                          capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip().splitlines()
        rep.add("release notes", FAIL, "; ".join(detail[-3:]) or "generator exited %d" % proc.returncode)
        return
    rep.add("release notes", PASS, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip()
            else "built and verified")


# --------------------------------------------------------------------------- the existing audits

def check_audits(rep, root=ROOT):
    """CALL the audits that already exist; do not grow a second copy of them here.

    `claims_audit.py` and `governance_audit.py` are the gate `release.yml` runs before it publishes,
    and they own the question "is every published claim still true?" -- including the counts quoted in
    README.md, MCP_LISTINGS.md and index.html. Reimplementing any of that here would give us two
    checkers that drift apart, which is the failure this whole file is about. The only thing added is
    that you find out BEFORE tagging instead of after.

    The third leg is theirs too: `GOV_FALSIFY=1` must make the governance audit FAIL. An audit that
    passes with its own defect injected has measured nothing, so a passing control is a FAILURE here.
    """
    legs = []
    for script in ("claims_audit.py", "governance_audit.py"):
        if not (root / script).exists():
            rep.add("audits", FAIL, "%s is missing; the release workflow runs it and this cannot" % script)
            return
        proc = subprocess.run([sys.executable, script, "--local"], cwd=str(root),
                              capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            tail = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if "FAIL" in ln]
            rep.add("audits", FAIL, "%s exited %d: %s" % (script, proc.returncode,
                                                          "; ".join(tail[-2:]) or "see its output"))
            return
        legs.append(script.split("_")[0])

    env = dict(os.environ, GOV_FALSIFY="1")
    control = subprocess.run([sys.executable, "governance_audit.py", "--local", "--repeats", "1"],
                             cwd=str(root), capture_output=True, text=True, errors="replace", env=env)
    if control.returncode == 0:
        rep.add("audits", FAIL,
                "GOV_FALSIFY=1 still PASSED -- the governance audit cannot detect its own defect, "
                "so its green above proves nothing")
        return
    rep.add("audits", PASS, "%s audits pass; the GOV_FALSIFY control failed as it must" % "+".join(legs))


# --------------------------------------------------------------------------- probe receipts

def _probe_snapshot(root):
    """Byte-exact contents of every probe receipt, as they are right now.

    A CHECKLIST MUST NOT DIRTY THE TREE IT IS CLEARING. You run this immediately before
    `git commit && git tag`, so anything it leaves behind gets committed into the release.

    Measured, not assumed -- and my first attribution was wrong. I recorded that the audits rewrote
    `probes/governance_sufficiency_bytes.json` because it appeared dirty right after an audits run;
    running each audit in isolation left it byte-identical. The actual writer is
    `probes/governance_sufficiency_probe.py`, which the SUITE executes: run it alone and the receipt's
    record ids and Ed25519 keys are regenerated, 45 lines of churn. So the snapshot wraps the WHOLE
    run, not one leg, because the leg I blamed was not the one writing.

    Bytes, not text, for two measured reasons: this receipt is not valid UTF-8 (0x97 at offset 2321),
    so a text reader cannot open it at all -- the first version of this crashed the checklist on it --
    and text mode on Windows rewrites every LF as CRLF, leaving a file with identical content and
    different bytes, permanently dirty. That is the bug `tools/mutation_check.py` documents at length.

    The snapshot is taken at the START OF THIS RUN, not from git, so a receipt a developer has already
    edited is restored to THEIR version and never to HEAD.
    """
    probes = root / "probes"
    out = {}
    for path in sorted(probes.glob("*.json")) if probes.is_dir() else []:
        try:
            out[path] = path.read_bytes()
        except OSError:
            pass
    return out


def restore_probe_snapshot(snapshot):
    """Put back only what changed. A file the run did not touch is not rewritten at all."""
    restored = []
    for path, blob in snapshot.items():
        try:
            if path.read_bytes() != blob:
                path.write_bytes(blob)
                restored.append(path.name)
        except OSError:
            pass
    return restored


# --------------------------------------------------------------------------- the suite

def check_tests(rep, root=ROOT, skip=False):
    if skip:
        rep.add("test suite", SKIP, "--skip-tests was passed; this run does NOT clear a release")
        return
    # `-rfE` so the run NAMES its failures and errors. Without it this reported "pytest exited 1:
    # 1 failed, 2702 passed" and nothing else -- a 20-minute check whose failure message does not
    # say what failed, which cost exactly one more 20-minute run to find out. `errors="replace"`
    # because a probe printed a cp1250 byte and the decode blew up inside subprocess's reader
    # thread, which loses the output the same way silence does.
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "-rfE"], cwd=str(root),
                          capture_output=True, text=True, errors="replace")
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else "no output"
    if proc.returncode != 0:
        named = [ln.strip() for ln in tail if ln.startswith(("FAILED ", "ERROR "))]
        detail = "pytest exited %d: %s" % (proc.returncode, summary)
        if named:
            detail += "\n" + "\n".join("      " + n for n in named[:15])
            if len(named) > 15:
                detail += "\n      ... and %d more" % (len(named) - 15)
        else:
            # A non-zero exit with nothing named is its own finding: a crash, a collection error or
            # a hang killed the run before the summary. Say so rather than implying a clean failure.
            detail += " (no FAILED/ERROR lines -- the run did not reach a summary)"
        rep.add("test suite", FAIL, detail)
        return
    rep.add("test suite", PASS, summary)


# --------------------------------------------------------------------------- main

def run(root=ROOT, skip_tests=False):
    print("pre-release checklist for inspeximus %s" % pyproject_version(root))
    print("  tree: %s\n" % root)
    rep = Report()
    snapshot = _probe_snapshot(root)
    try:
        check_version_carriers(rep, root)
        check_import_version(rep, root)
        check_changelog(rep, root)
        check_zero_dependencies(rep, root)
        check_mcp_server(rep, root)
        check_audits(rep, root)
        check_release_notes(rep, root)
        check_tests(rep, root, skip=skip_tests)
    finally:
        # Reported, never silent: a guard that quietly repairs something teaches nobody it fired.
        restored = restore_probe_snapshot(snapshot)
        if restored:
            print("\n  (restored %d probe receipt(s) this run rewrote: %s)"
                  % (len(restored), ", ".join(restored)))
    code = rep.exit_code()
    print("")
    if code == 0:
        print("READY: every check ran and passed. Nothing here publishes anything -- "
              "tagging and PyPI stay a deliberate, separate act.")
    elif code == 1:
        print("NOT READY: %d check(s) FAILED." % sum(1 for _, s, _ in rep.rows if s == FAIL))
    else:
        print("NOT CLEARED: %d check(s) were SKIPPED and did not run."
              % sum(1 for _, s, _ in rep.rows if s == SKIP))
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip the pytest leg (fast iteration). The run then exits 2, never 0.")
    ap.add_argument("--root", default=str(ROOT), help="tree to check (default: this repository)")
    args = ap.parse_args(argv)
    return run(pathlib.Path(args.root).resolve(), skip_tests=args.skip_tests)


if __name__ == "__main__":
    raise SystemExit(main())
