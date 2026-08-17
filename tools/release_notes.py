"""Turn a CHANGELOG entry into release notes a reader can act on -- and refuse to ship one that lies.

The changelog is written for us: a record of what we found and repaired, in the order we found it. The
reader arriving from PyPI, GitHub Releases or the MCP registry is answering a different question --
"should I install this, and what will it cost me?" -- and four frames answer it: who should upgrade,
what changed, what breaks, and the one command to try it.

That moment is worth framing. From an analysis of this package's public PyPI download series, the
release day is the entire adoption signal: 555 downloads/day on release days against 9 on quiet days,
r=0.977. That figure is reproduced as reported -- nothing in this repository recomputes it, and its
window and n are not recorded; see RELEASING.md. Capability is not what moves it; a release is.

This fills `docs/RELEASE_NOTES_TEMPLATE.md` from the changelog, DERIVING what the changelog states and
emitting a `TODO(...)` -- which `--check` rejects -- for anything it does not. It never invents an
audience, a benefit or a number.

    python tools/release_notes.py                 # print the notes for the version in pyproject.toml
    python tools/release_notes.py 1.88.1          # ... for a specific version
    python tools/release_notes.py --out NOTES.md  # write them
    python tools/release_notes.py --check         # verify only: exit non-zero on any defect

`--check` is the gate `tools/release_check.py` runs. It verifies: every section present, no unfilled
TODO, no unverifiable-claim language, and -- the leg with real teeth -- it EXECUTES the Python example
and compares its stdout against the `# -> ` line the notes promise.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_REL = pathlib.Path("docs") / "RELEASE_NOTES_TEMPLATE.md"
TEMPLATE = ROOT / TEMPLATE_REL


def template_for(root=ROOT):
    """The template belonging to THE TREE BEING RELEASED.

    This was a module constant pinned to the script's own repository, so `--root OTHER` read the
    changelog from OTHER and the template from here -- notes assembled from two different trees, and
    the mismatch would have been invisible in the output. Same class as everything else in this
    changeset: a reader pointed somewhere other than where the caller thinks it is pointing.
    """
    path = root / TEMPLATE_REL
    if not path.exists():
        raise SystemExit("::error::no release-notes template at %s; the notes have no shape to fill"
                         % path)
    return path

VERSION_HEADING = re.compile(r'^## (\d+\.\d+\.\d+)\b(.*)$', re.M)

REQUIRED_SECTIONS = ("## Who should upgrade", "## What changed", "## What breaks",
                     "## Try it", "## Check it yourself")

# Rule 2 of the template: no claim a reader cannot check. Each of these asserts a quality with no
# procedure behind it, or asserts something about somebody else's product that we have not measured.
# Kept deliberately narrow -- a lint that fires on ordinary technical prose gets switched off, and a
# gate that is switched off is not a gate. Measured across all 107 version entries in CHANGELOG.md:
# zero hits, so it costs the existing corpus nothing. `tests/test_release_check_has_teeth.py` carries
# the other half of that measurement -- a positive control proving it still fires on inflated text.
INFLATION = (
    r"revolutionar", r"game[- ]chang", r"blazing", r"blazingly", r"seamless", r"effortless",
    r"unparalleled", r"cutting[- ]edge", r"best[- ]in[- ]class", r"world[- ]class",
    r"industry[- ]leading", r"next[- ]generation", r"supercharg", r"unlock the power",
    r"lightning[- ]fast", r"rock[- ]solid", r"bulletproof", r"magical",
    # the comparative negative: a claim about what other people cannot do, which we have not measured
    # and cannot maintain. Say what inspeximus DOES -- deterministically, zero-LLM, in a single file.
    r"competitors?\s+(?:can'?t|cannot|can not)", r"no one else can", r"nobody else",
    r"no other (?:library|tool|product)", r"only (?:we|inspeximus) can",
)


def pyproject_version(root=ROOT):
    m = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("::error::pyproject.toml has no version")
    return m.group(1)


def entry_for(version, root=ROOT):
    """-> (heading_tail, body) for `## <version> - <heading tail>`, or (None, None).

    Headings that do not begin with a version -- `## Audit notes from the day 1.87.0 was assembled` --
    are not entries and are skipped, so a section boundary is never taken from one.
    """
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    hits = list(VERSION_HEADING.finditer(text))
    for i, m in enumerate(hits):
        if m.group(1) == version:
            end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
            return m.group(2).lstrip(" -—").strip(), text[m.end():end].strip()
    return None, None


def who_should_upgrade(heading, version):
    """Derived, never invented.

    Our entries lead with the audience by convention -- `## 1.89.0 - UPGRADE IF YOU USE slash()/restore():
    ...`. When the heading says it, use it. When it does not, say so with a TODO rather than writing
    "everyone should upgrade", which would be a claim nobody measured.
    """
    m = re.search(r'\bUPGRADE\s+(IF|WHEN)\b(.*)', heading, re.I)
    if m:
        # Split at the first colon-space, with NO lookbehind. The lookbehind this used to carry
        # required a lowercase letter, `)` or backtick immediately before the colon, so
        # `UPGRADE IF YOU RELY ON ERASURE: an erasure left...` did not split at all and the audience
        # line swallowed the entire headline. Measured over every entry that carries the convention
        # (1.89.0, 1.88.1, 1.88.0, 1.87.0), the plain rule cuts all four correctly.
        clause = re.split(r':\s', m.group(2), maxsplit=1)[0].strip().rstrip(".:")
        # The author's capitals are emphasis, not a sentence, so they are quoted rather than reworded
        # into "Upgrade if YOU USE ...", which read as a typo. Their words, unedited.
        return "Upgrade %s this is true of you: **%s**." % (m.group(1).lower(), clause)
    # THE SECOND REMEDY THE MESSAGE BELOW NAMES, now actually implemented. It offered "say plainly that
    # it affects nobody's code" while only `UPGRADE IF/WHEN` was accepted, so a release that genuinely
    # changes no user-visible behaviour had no way to pass except by inventing an audience for it. An
    # error message that names a fix the code rejects sends the reader in a circle.
    if re.search(r"AFFECTS NOBODY'?S CODE", heading, re.I):
        return ("**No upgrade needed.** This release changes nothing a caller can observe; it is here "
                "so the record is complete.")
    return ("TODO(who should upgrade): the %s changelog heading does not say who this is for. "
            "Name the users affected, or say plainly that it AFFECTS NOBODY'S CODE." % version)


def previous_version(version, root=ROOT):
    """The entry immediately below this one, or None if this is the first."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    vers = [m.group(1) for m in VERSION_HEADING.finditer(text)]
    for i, v in enumerate(vers):
        if v == version:
            return vers[i + 1] if i + 1 < len(vers) else None
    return None


def bump_class(version, previous):
    """'major' | 'minor' | 'patch' | None -- arithmetic, so it has no false positives."""
    if not previous:
        return None
    try:
        new = [int(p) for p in version.split(".")[:3]]
        old = [int(p) for p in previous.split(".")[:3]]
    except ValueError:
        return None
    if new[0] != old[0]:
        return "major"
    if new[1] != old[1]:
        return "minor"
    return "patch"


MARKER = re.compile(r'\b(BEHAVIOUR CHANGE|BEHAVIOR CHANGE|BREAKING)\b')


def what_breaks(body, version, root=ROOT, heading=""):
    """Only what the entry MARKS. Anything else would be us asserting a compatibility promise from prose.

    RELEASING.md already requires a behaviour change to ship with a `BEHAVIOUR CHANGE` line at the top
    of its entry, so this reads the convention the house already has: it reports those lines verbatim,
    and otherwise reports the absence AS an absence -- a statement about the changelog, which a reader
    can check against the source, not a promise about the code, which they cannot.

    It deliberately does NOT guess from prose. Measured across the 107 entries in CHANGELOG.md, the
    break-shaped phrases score far too high to gate on: "no longer" appears in 17% of entries and
    "removed" in 9%, mostly describing fixes ("unresolvable parents are no longer dropped silently").
    A section that demanded an answer on a quarter of releases would be switched off within a month,
    and a gate that is switched off is not a gate. The one hard rule left is arithmetic: under this
    project's own scheme (CHANGELOG.md, line 3) MAJOR means breaking, so a major bump with no marker
    is a contradiction the author has to resolve.
    """
    # THE HEADING IS PART OF THE ENTRY. This scanned the body only, and measuring the corpus rather
    # than trusting the convention showed 2 of the 9 marked entries -- 1.73.0 and 1.70.0 -- carry
    # `(BEHAVIOUR CHANGE)` in the HEADING and nowhere else. On those two releases the section would
    # have reported "no marker" about an entry that declared one in its title: a check that cannot see
    # its target, reporting safe.
    marked = [ln.strip() for ln in ([heading] if heading else []) + body.splitlines()
              if MARKER.search(ln)]
    if marked:
        return "\n".join("- " + ln for ln in marked)
    bump = bump_class(version, previous_version(version, root))
    if bump == "major":
        return ("TODO(what breaks): %s is a MAJOR bump, which this project's own scheme defines as "
                "breaking, and no line in the entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. "
                "Mark what breaks, or explain why the major number moved without one." % version)
    out = ("No line in the %s changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. "
           "That is a statement about the entry, which you can check against the source, and it is "
           "the only claim this section will make for you." % version)
    if bump == "minor":
        out += ("\n\nIf that is wrong -- if something a caller relies on changed shape, name or "
                "default -- the entry is what needs fixing, not this section: RELEASING.md requires "
                "a behaviour change to carry the marker on its own line, and this reads that marker.")
    return out


def render(version=None, root=ROOT):
    version = version or pyproject_version(root)
    heading, body = entry_for(version, root)
    if body is None:
        raise SystemExit("::error::CHANGELOG.md has no `## %s` entry; write it before the notes" % version)
    text = template_for(root).read_text(encoding="utf-8")
    text = re.sub(r'<!--.*?-->\n?', "", text, flags=re.S)          # the rules stay in the template
    for key, value in (("VERSION", version),
                       ("HEADLINE", heading or "TODO(headline): the entry has no summary line."),
                       ("WHO_SHOULD_UPGRADE", who_should_upgrade(heading or "", version)),
                       ("WHAT_CHANGED", body),
                       ("WHAT_BREAKS", what_breaks(body, version, root, heading or ""))):
        text = text.replace("{{%s}}" % key, value)
    return text.lstrip()


# --------------------------------------------------------------------------- the gate

def python_example(notes):
    """The fenced ```python block and the expected stdout from its trailing `# -> ` line."""
    m = re.search(r'```python\n(.*?)```', notes, re.S)
    if not m:
        return None, None
    code = m.group(1)
    exp = re.findall(r'^# -> (.*)$', code, re.M)
    return code, (exp[-1].strip() if exp else None)


def verify(notes, version, root=ROOT):
    """-> list of problems. Empty means the notes are shippable."""
    problems = []
    for section in REQUIRED_SECTIONS:
        if section not in notes:
            problems.append("missing section: %s" % section)
    for todo in re.findall(r'TODO\([^)]*\)[^\n]*', notes):
        problems.append("unfilled: %s" % todo)
    for pat in INFLATION:
        for hit in re.findall(pat, notes, re.I):
            problems.append("unverifiable-claim language: %r (rule 2 of the template)"
                            % (hit if isinstance(hit, str) else pat))
    if "{{" in notes:
        problems.append("an unreplaced placeholder is still in the output: %s"
                        % re.findall(r'\{\{[^}]*\}\}', notes)[:3])
    if version not in notes:
        problems.append("the notes never name the version being released (%s)" % version)

    code, expected = python_example(notes)
    if code is None:
        problems.append("the notes carry no runnable python example")
    elif expected is None:
        problems.append("the python example states no expected output (`# -> ...`), so running it "
                        "could not tell success from silence")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True,
                                  text=True, env=_env(root))
            if proc.returncode != 0:
                last = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
                problems.append("the python example does not run: %s" % last)
            elif proc.stdout.strip() != expected:
                problems.append("the python example printed %r; the notes promise %r"
                                % (proc.stdout.strip(), expected))
    return problems


def _env(root):
    env = dict(os.environ)
    # Run the example against THIS tree, not against whatever is installed in site-packages: the notes
    # describe the release being cut, and an installed copy would answer for a different one.
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("version", nargs="?", help="default: the version in pyproject.toml")
    ap.add_argument("--out", help="write the notes here instead of printing them")
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit non-zero on any defect (this is the release gate)")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    version = args.version or pyproject_version(root)
    notes = render(version, root)
    problems = verify(notes, version, root)

    if args.check:
        for p in problems:
            print("::error::%s" % p)
        if problems:
            print("release notes for %s: %d problem(s)" % (version, len(problems)))
            return 1
        code, expected = python_example(notes)
        print("release notes for %s build clean: %d sections, example ran and printed %s"
              % (version, len(REQUIRED_SECTIONS), expected))
        return 0

    if problems:                                   # never write notes we would refuse to ship
        for p in problems:
            print("::error::%s" % p, file=sys.stderr)
        return 1
    if args.out:
        pathlib.Path(args.out).write_text(notes, encoding="utf-8")
        print("wrote %s (%d characters)" % (args.out, len(notes)))
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    # This tool hit the exact defect 2.13.0 fixes in the CLI, on the character 2.13.0's own changelog
    # entry uses as its example: printing the notes raised UnicodeEncodeError on a cp1250 console and
    # exited 1, so a release could be blocked by its own release notes rendering. The guard is
    # IMPORTED rather than re-implemented -- two copies of one decision is how the first one stops
    # getting fixed.
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from inspeximus.cli import _survive_a_narrow_console
    _survive_a_narrow_console()
    raise SystemExit(main())
