"""Measure our README against a real sample, with ONE instrument applied to every repo.

The first comparison covered three direct competitors and I reported a lead from it. Three is a small
sample and, more importantly, they are all in our own category -- the repos people actually cite for
README craft are elsewhere. So this widens it to ten and, crucially, stops asking a model to count:
every number below comes from the same function run over the same raw markdown, so the comparison is
reproducible rather than described.

Deliberately countable axes only. "Is it well written" is not on the list because I cannot measure it
and would only be flattering myself.
"""
import io
import json
import re
import subprocess
import sys

REPOS = [
    # direct competitors
    ("mem0ai/mem0", "main"), ("getzep/graphiti", "main"), ("topoteretes/cognee", "main"),
    ("letta-ai/letta", "main"),
    # repos widely cited for README/docs craft, outside our category on purpose
    ("astral-sh/ruff", "main"), ("astral-sh/uv", "main"), ("Textualize/rich", "master"),
    ("tiangolo/fastapi", "master"), ("pydantic/pydantic", "main"), ("chroma-core/chroma", "main"),
]

BADGE = re.compile(r"\[!\[")
IMG = re.compile(r"<img\s|<picture>|!\[(?!\[)")
FENCE = re.compile(r"^```", re.M)   # re.M or ^ anchors to the string, not each line
H2 = re.compile(r"^##\s", re.M)
NUM_TABLE = re.compile(r"^\|.*\d.*\|", re.M)
SOCIAL = re.compile(r"discord|slack\.|sponsor|ycombinator|trendshift|producthunt", re.I)
CITE = re.compile(r"doi\.org|arxiv\.org|CITATION\.cff|bibtex|@article|@software", re.I)


def measure(text):
    lines = text.splitlines()
    first_code = next((i + 1 for i, l in enumerate(lines) if FENCE.match(l)), None)
    head = "\n".join(lines[:30])
    # an image that is NOT a badge: strip badge constructs first, then look
    head_no_badges = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)|!\[[^\]]*\]\(https://img\.shields\.io[^)]*\)",
                            "", head)
    return {
        "lines": len(lines),
        "to_first_code": first_code if first_code else 0,
        "badges": len(BADGE.findall(text)),
        "visual_top": bool(IMG.search(head_no_badges)),
        "num_table": bool(NUM_TABLE.search(text)),
        "h2": len(H2.findall(text)),
        "citation": bool(CITE.search(text)),
        "social": bool(SOCIAL.search(text)),
        "code_blocks": len(FENCE.findall(text)) // 2,
    }


def fetch(repo, branch):
    for b in (branch, "main", "master"):
        url = f"https://raw.githubusercontent.com/{repo}/{b}/README.md"
        r = subprocess.run(["curl", "-sfL", url], capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and len(r.stdout) > 400:
            return r.stdout
    return None


rows = []
ours = measure(io.open(sys.argv[1] if len(sys.argv) > 1 else "README.md", encoding="utf-8").read())
rows.append(("inspeximus (OURS)", ours))
for repo, branch in REPOS:
    t = fetch(repo, branch)
    if t is None:
        print(f"  !! could not fetch {repo} -- EXCLUDED, not silently counted as absent", flush=True)
        continue
    rows.append((repo, measure(t)))

hdr = f"{'repo':<24}{'lines':>7}{'→code':>7}{'badges':>8}{'visual':>8}{'table':>7}{'H2':>5}{'cite':>6}{'social':>8}{'code':>6}"
print(hdr)
print("-" * len(hdr))
for name, m in rows:
    print(f"{name:<24}{m['lines']:>7}{m['to_first_code']:>7}{m['badges']:>8}"
          f"{'yes' if m['visual_top'] else 'no':>8}{'yes' if m['num_table'] else 'no':>7}"
          f"{m['h2']:>5}{'yes' if m['citation'] else 'no':>6}"
          f"{'yes' if m['social'] else 'no':>8}{m['code_blocks']:>6}")

n = len(rows) - 1
print(f"\nsample: {n} comparison repos fetched and measured with the same function.")
others = [m for name, m in rows[1:]]
if others:
    faster = sum(1 for m in others if ours["to_first_code"] < m["to_first_code"])
    print(f"  we reach the first code block sooner than {faster}/{n}")
    print(f"  repos with a numeric table : {sum(1 for m in others if m['num_table'])}/{n} (ours: "
          f"{'yes' if ours['num_table'] else 'no'})")
    print(f"  repos with a visual up top : {sum(1 for m in others if m['visual_top'])}/{n} (ours: "
          f"{'yes' if ours['visual_top'] else 'no'})")
    print(f"  repos with a citation      : {sum(1 for m in others if m['citation'])}/{n} (ours: "
          f"{'yes' if ours['citation'] else 'no'})")
    print(f"  repos with social proof    : {sum(1 for m in others if m['social'])}/{n} (ours: "
          f"{'yes' if ours['social'] else 'no'})")
    print(f"  median length              : {sorted(m['lines'] for m in others)[n // 2]} lines "
          f"(ours: {ours['lines']})")
