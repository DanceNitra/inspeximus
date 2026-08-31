"""Generate docs/CORE_MAP.md from the AST of inspeximus/core.py, so the map cannot drift from the code.

WHY THIS EXISTS, and why it is not a file split.

`core.py` is 963 KB and the product's pitch is *auditable, reproducible, zero-dependency*. The obvious
reading is that a megabyte contradicts the pitch, and the first version of our product plan put
"split core.py" at the top of the list. An audit refuted the premise: **most of that file is
explanatory prose** -- the exact split is measured below rather than asserted. The megabyte is not
opacity. It IS the audit trail, and what it lacked was a table of contents.

Splitting it properly costs 14 to 20 hours, moves 56 names that 70 test files import from
`inspeximus.core` (28 of them private), and carries one specific trap: `perf/gate.py` patches
`core._dump_store`, so relocating `_save` would leave that patch matching nothing while the perf gate
still reported PASS -- a check that no longer sees its target, which is the oldest failure class in
this repository. None of that risk buys a user anything. This does, in about a second.

NO TAXONOMY, ON PURPOSE. The first version of this tool sorted methods into twelve named subsystems
by name-prefix, and the largest group that produced was "other": 88 of 224 methods and 245 KB,
including real API surface such as `confirm`, `inclusion_proof`, `detect_split_view` and
`decisions_in_force`. Its line ranges also overlapped almost completely, because a prefix rule
scatters methods that sit together in the file. Those buckets were a guess about the code rather
than a fact about it, and core.py carries no section banners of its own to borrow -- one dashed
comment line in 13,640. So this reports where things ARE, in the order they are, and cannot be wrong
about a category it never invents.

    python tools/gen_core_map.py            # write docs/CORE_MAP.md
    python tools/gen_core_map.py --check    # exit 1 if the committed map is stale

stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "inspeximus", "core.py")
OUT = os.path.join(HERE, "docs", "CORE_MAP.md")

BLOCK_TARGET = 60_000   # bytes per block, so a 963 KB file yields a readable number of rows


def measure(src: str, tree: ast.Module) -> dict:
    lines = src.splitlines()
    total = len(src.encode("utf-8"))
    doc = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                doc += len(d.encode("utf-8"))
    com = sum(len(l.encode("utf-8")) for l in lines if l.lstrip().startswith("#"))
    return {"total": total, "lines": len(lines), "doc": doc, "com": com,
            "code": total - doc - com}


def build() -> str:
    src = io.open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    m = measure(src, tree)

    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Inspeximus"), None)
    if cls is None:
        raise SystemExit("REFUSED: class Inspeximus not found; the map would describe nothing")

    methods = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        size = sum(len(lines[i].encode("utf-8")) + 1
                   for i in range(node.lineno - 1, min(end, len(lines))))
        # A property getter and its setter share a name. Listing both made `items` appear twice in
        # the map, which reads as a duplicate definition -- a defect the map would have been
        # inventing. Mark the pair instead of double-counting it.
        deco = {getattr(d, "attr", getattr(d, "id", "")) for d in node.decorator_list}
        kind = "property" if ("property" in deco or "setter" in deco) else ""
        methods.append((node.name, node.lineno, end, size, kind))
    methods.sort(key=lambda t: t[1])
    seen_prop = set()
    deduped = []
    for name, lo, hi, size, kind in methods:
        if kind == "property":
            if name in seen_prop:
                deduped[-1] = (deduped[-1][0], deduped[-1][1], hi,
                               deduped[-1][3] + size, "property")
                continue
            seen_prop.add(name)
        deduped.append((name, lo, hi, size, kind))
    methods = deduped

    # Contiguous blocks in SOURCE ORDER. The cut points are arbitrary; the order is the file's own,
    # and the order is what a reader navigates by.
    blocks, cur, run = [], [], 0
    for mth in methods:
        cur.append(mth)
        run += mth[3]
        if run >= BLOCK_TARGET:
            blocks.append((cur, run))
            cur, run = [], 0
    if cur:
        blocks.append((cur, run))

    tops = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    others = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and n.name != "Inspeximus"]
    pub_all = sorted((n, ln) for n, ln, _, _, _ in methods if not n.startswith("_"))

    def pct(b):
        return f"{100.0 * b / m['total']:.0f}%"

    out = [
        "# core.py, mapped",
        "",
        "**Generated by `tools/gen_core_map.py` from the AST. Do not edit by hand.**",
        "CI runs `python tools/gen_core_map.py --check`, so this file fails the build when it stops",
        "matching the code rather than quietly becoming fiction.",
        "",
        "## Why one big file, and why the size is not the problem",
        "",
        f"`inspeximus/core.py` is **{m['total']:,} bytes** across **{m['lines']:,} lines**. Of that:",
        "",
        "| | bytes | share |",
        "|---|---:|---:|",
        f"| comments | {m['com']:,} | {pct(m['com'])} |",
        f"| docstrings | {m['doc']:,} | {pct(m['doc'])} |",
        f"| executable code | {m['code']:,} | {pct(m['code'])} |",
        "",
        f"**{pct(m['doc'] + m['com'])} of the file is explanatory prose.** Nearly every guarantee",
        "carries the reason it exists and, usually, the failure that produced it. That is the audit",
        "trail, not padding: deleting it to make the file smaller would remove exactly what makes the",
        "code checkable. What the file lacked was a table of contents.",
        "",
        "## Where things are",
        "",
        "Contiguous blocks in source order. Cut points are arbitrary, roughly 60 KB each; the order is",
        "the file's own. Public methods are named, private ones counted; `*` marks a property.",
        "",
        "| block | lines | bytes | public methods |",
        "|---:|---|---:|---|",
    ]
    for n, (mths, size) in enumerate(blocks, 1):
        lo, hi = mths[0][1], mths[-1][2]
        pub = [x[0] + ("*" if x[4] == "property" else "") for x in mths if not x[0].startswith("_")]
        priv = len(mths) - len(pub)
        shown = ", ".join(f"`{x}`" for x in pub[:7])
        if len(pub) > 7:
            shown += f", +{len(pub) - 7} more"
        if priv:
            shown += f" _(+{priv} private)_" if shown else f"_{priv} private only_"
        out.append(f"| {n} | {lo}–{hi} | {size:,} | {shown} |")

    out += [
        "",
        f"**{len(methods)} methods** on `Inspeximus`: {len(pub_all)} public, "
        f"{len(methods) - len(pub_all)} private. Top-level functions outside the class: "
        f"**{len(tops)}**. Other classes: **{len(others)}**"
        + (f" ({', '.join(others)})." if others else "."),
        "",
        "## Index",
        "",
        "Every public method, alphabetically, with the line it starts on.",
        "",
        "".join(f"`{n}` {ln} · " for n, ln in pub_all).rstrip(" ·"),
        "",
    ]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed map differs from what the code produces now")
    a = ap.parse_args()
    fresh = build()
    if a.check:
        if not os.path.exists(OUT):
            print(f"REFUSED: {OUT} does not exist. Run without --check to create it.")
            return 1
        if io.open(OUT, encoding="utf-8").read().replace("\r\n", "\n") == fresh:
            print("core map is current")
            return 0
        print("STALE: docs/CORE_MAP.md no longer describes inspeximus/core.py.")
        print("  Regenerate with: python tools/gen_core_map.py")
        # SHOW WHAT DIFFERS. "Stale" alone costs a round trip per guess, and it hid a real defect:
        # this file failed on Python 3.9 while passing on 3.10 through 3.12, so the generator was
        # interpreter-dependent and the message said nothing that would lead anyone to that.
        import difflib
        on_disk = io.open(OUT, encoding="utf-8").read().replace(chr(13) + chr(10), chr(10)).splitlines()
        diff = list(difflib.unified_diff(on_disk, fresh.splitlines(), "on disk",
                                         "generated by python %d.%d" % sys.version_info[:2],
                                         lineterm="", n=1))
        print("  running on python %d.%d" % sys.version_info[:2])
        for line in diff[:40]:
            print("  " + line)
        if len(diff) > 40:
            print("  ... %d more diff line(s)" % (len(diff) - 40))
        return 1
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(fresh)
    print(f"wrote {OUT} ({len(fresh):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
