"""Attack every regular expression in inspeximus with growing input built from its own keywords (3.21.1).

Not a test module: tests/test_3211_no_pattern_in_the_package_backtracks.py runs it, and
`python tests/_redos_attack.py` prints the full table.

WHAT IT COLLECTS
  * every compiled re.Pattern reachable from a module's globals, from lists, tuples, dicts and sets in them, and from
    class attributes, by importing every module of the package;
  * conservatively, every string literal in a module that imports `re` that compiles as a pattern and holds a regex
    construct (`\\`, `[`, `(?`, `*`, `+`, `?` or `{`), whether or not the data flow from it to an `re` call is traced.
    AUDIT-A found the hook's `_PRE_PATTERNS`, strings in a list handed to re.search at run time, outside the first
    scan's reach; a literal anywhere is now in;
  * every object in the package that stands in for a regex (a `.search` and a `.pattern`), attacked through its own
    `.search`, which is the code that runs.
  Docstrings are not collected.

HOW IT ATTACKS
  Each pattern gets pumps from its own literal keywords (AUDIT-A: the first scan's pumps never held a whole keyword):
  each literal run of two or more characters, the sample string the whole pattern generates, and the same joined to a
  separator, plus common single characters. A pump is repeated to 20,000 characters, with and without a failing
  suffix. A pattern under 2 ms on every pump is linear on it with a wide margin: a quadratic pattern costs seconds at
  that size. One over 2 ms is timed at n, 2n and 4n (the minimum of 3 runs each), and it grows worse than linear when
  both doublings take more than 3 times as long and the largest call takes over 20 ms."""
import ast
import importlib
import math
import os
import pkgutil
import re
import sys
import time

try:
    import re._parser as _sre_parse                           # Python 3.11+
    from re._constants import (MAX_REPEAT, MIN_REPEAT, SUBPATTERN, BRANCH, LITERAL, IN, ANY, CATEGORY, NOT_LITERAL,
                               RANGE, NEGATE, GROUPREF, ASSERT, ASSERT_NOT)
except ImportError:                                           # pragma: no cover - Python 3.9 and 3.10
    import sre_parse as _sre_parse
    from sre_constants import (MAX_REPEAT, MIN_REPEAT, SUBPATTERN, BRANCH, LITERAL, IN, ANY, CATEGORY, NOT_LITERAL,
                               RANGE, NEGATE, GROUPREF, ASSERT, ASSERT_NOT)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "inspeximus")
REGEXISH = re.compile(r"\\|\[|\(\?|[*+?{]")
SEPARATORS = (" ", "\n", ";", "|", "", "a")
SINGLES = (" ", "\n", "\t", "a", "1", ".", "-", "<a>", "a=", "a;", "|", "/")
SUFFIXES = ("", "\x00!")
SCREEN_CHARS = 10000
FUNCS = frozenset(("compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"))
SCREEN_SECONDS = 0.002
SLOW_SECONDS = 0.5


def _is_docstring(node, parents):
    p = parents.get(node)
    return isinstance(p, ast.Expr) and isinstance(parents.get(p), (ast.Module, ast.ClassDef, ast.FunctionDef,
                                                                       ast.AsyncFunctionDef))


def literal_strings():
    """{(pattern, flags): 'file:line'} for every regex-looking string literal in a module that imports re."""
    out = {}
    for dp, _d, fs in os.walk(PKG):
        for f in sorted(fs):
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src)
            imports_re = False
            for n in ast.walk(tree):
                if isinstance(n, ast.Import) and any(a.name == "re" or a.name.startswith("re.") for a in n.names):
                    imports_re = True
                elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "re":
                    imports_re = True
            if not imports_re:
                continue
            parents = {}
            for n in ast.walk(tree):
                for c in ast.iter_child_nodes(n):
                    parents[c] = n
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and REGEXISH.search(n.value):
                    if _is_docstring(n, parents) or len(n.value) > 2000:
                        continue
                    # An operand of `+` is a fragment of a longer pattern (_secrets builds its patterns this way):
                    # the compiled whole is collected by the runtime pass, and a fragment alone is not what runs.
                    if isinstance(parents.get(n), ast.BinOp) and isinstance(parents[n].op, ast.Add):
                        continue
                    # An argument to a call that is not an `re` function is a message, a file mode or the reference
                    # copy handed to a stand-in (whose own .search is attacked). Patterns that a helper compiles are
                    # collected when the result is kept in a module global; the rest are the ones read by hand.
                    par = parents.get(n)
                    if isinstance(par, ast.keyword):
                        par = parents.get(par)
                    if isinstance(par, ast.Call) and not (isinstance(par.func, ast.Attribute) and
                                                          isinstance(par.func.value, ast.Name) and
                                                          par.func.value.id == "re"):
                        continue
                    try:
                        re.compile(n.value)
                    except (re.error, OverflowError, RecursionError):
                        continue
                    out.setdefault((n.value, 0), "%s:%d" % (rel, n.lineno))
    return out


def _fold(node, consts):
    """The string a module-level expression evaluates to, when it is literals, `+` of literals or names of such:
    `_PAT = r"\\bdemoe\\b" + r"..."` (AUDIT-A R2-1 c). None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _fold(node.left, consts), _fold(node.right, consts)
        return a + b if a is not None and b is not None else None
    return None


def _re_names(tree):
    """The names that mean the `re` module or one of its functions here: `import re`, `import re as _re`,
    `from re import compile as c`."""
    mods, funcs = set(), {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "re":
                    mods.add(a.asname or "re")
        elif isinstance(n, ast.ImportFrom) and n.module == "re":
            for a in n.names:
                if a.name in FUNCS:
                    funcs[a.asname or a.name] = a.name
    return mods, funcs


def dynamic_sites(sources=None):
    """({(file, enclosing function, pattern source): line}, {(folded pattern, 0): 'file:line'}).

    Every call of an `re` function whose pattern argument is not a plain string literal is a SITE: a pattern built
    by `+`, by `join`, by `format`, by an f-string, by a helper or from a parameter (AUDIT-A R2-1). The test requires
    each site to be on a reviewed allowlist, so a new one fails until someone reads it. A site whose argument is a
    module-level string that folds to a literal (`_PAT = r"..." + r"..."`) is not a site: the folded pattern is
    returned and attacked like a literal. `sources` maps a name to source text, for the controls; by default every
    module of the package is read."""
    if sources is None:
        sources = {}
        for dp, _d, fs in os.walk(PKG):
            for f in sorted(fs):
                if f.endswith(".py"):
                    p = os.path.join(dp, f)
                    sources[os.path.relpath(p, ROOT).replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    sites, folded = {}, {}
    for rel, src in sources.items():
        tree = ast.parse(src)
        mods, funcs = _re_names(tree)
        if not mods and not funcs:
            continue
        consts = {}
        for st in tree.body:                                   # module-level strings, in order
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                v = _fold(st.value, consts)
                if v is not None:
                    consts[st.targets[0].id] = v
        parents = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parents[c] = n

        def where(node):
            names = []
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(node.name)
            return ".".join(reversed(names)) or "<module>"
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            is_re = (isinstance(f, ast.Attribute) and f.attr in FUNCS and isinstance(f.value, ast.Name)
                     and f.value.id in mods) or (isinstance(f, ast.Name) and f.id in funcs)
            if not is_re:
                continue
            arg = n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "pattern"), None)
            if arg is None or (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            value = _fold(arg, consts)                         # a name of, or a `+` of, module-level literals
            if value is not None:
                folded.setdefault((value, 0), "%s:%d" % (rel, n.lineno))
                continue
            sites[(rel, where(n), ast.unparse(arg))] = n.lineno
    return sites, folded


def runtime_objects():
    """({(pattern, flags): where} for compiled patterns, [(where, obj)] for regex stand-ins), and modules not imported."""
    sys.path.insert(0, ROOT)
    import inspeximus
    pats, objs, failed, seen = {}, [], [], set()

    def walk(obj, where, depth):
        if isinstance(obj, re.Pattern):
            if isinstance(obj.pattern, str):
                pats.setdefault((obj.pattern, obj.flags & ~re.UNICODE), where)
            return
        if hasattr(obj, "search") and isinstance(getattr(obj, "pattern", None), str) and not isinstance(obj, type):
            if id(obj) not in seen:
                seen.add(id(obj))
                objs.append((where, obj))
                for v in list(getattr(obj, "__dict__", {}).values()):     # the parts of a stand-in, e.g. _AnyOf's
                    walk(v, where, depth - 1)
            return
        if depth == 0:
            return
        if isinstance(obj, dict):
            for v in list(obj.values()):
                walk(v, where, depth - 1)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for v in list(obj):
                walk(v, where, depth - 1)
        elif isinstance(obj, type) and (obj.__module__ or "").startswith("inspeximus"):
            for k, v in list(vars(obj).items()):
                walk(v, where + "." + obj.__name__, depth - 1)
    for mi in pkgutil.walk_packages(inspeximus.__path__, "inspeximus."):
        try:
            mod = importlib.import_module(mi.name)
        except Exception as exc:                              # noqa: BLE001 -- an extra that is not installed
            failed.append("%s (%s)" % (mi.name, type(exc).__name__))
            continue
        for k, v in list(vars(mod).items()):
            walk(v, mi.name + "." + k, 3)
    return pats, objs, failed


# ---- pumps ----
def _gen(sub, groups=None):
    """A short string the parsed pattern matches, best effort. A back-reference repeats its group's text (without it
    the old frame regex's sample closed its tag as `</>` and the attack never formed a block)."""
    groups = {} if groups is None else groups
    out = []
    for op, av in sub:
        if op == GROUPREF:
            out.append(groups.get(av, ""))
            continue
        if op == LITERAL:
            out.append(chr(av))
        elif op == NOT_LITERAL:
            out.append("a" if av != ord("a") else "b")
        elif op == ANY:
            out.append("a")
        elif op == IN:
            neg = any(o == NEGATE for o, _ in av)
            ch = "a"
            for o, v in av:
                if o == LITERAL and not neg:
                    ch = chr(v)
                    break
                if o == RANGE and not neg:
                    ch = chr(v[0])
                    break
                if o == CATEGORY and not neg:          # a bare \s or \d parses as a set holding one category
                    ch = {"CATEGORY_DIGIT": "1", "CATEGORY_SPACE": " "}.get(str(v), "a")
                    break
            out.append(ch)
        elif op == CATEGORY:
            out.append({"CATEGORY_DIGIT": "1", "CATEGORY_SPACE": " "}.get(str(av).split(".")[-1], "a"))
        elif op in (MAX_REPEAT, MIN_REPEAT):
            out.append(_gen(av[2], groups) * max(1, min(av[0], 2)))
        elif op == SUBPATTERN:
            text = _gen(av[-1], groups)
            if av[0]:
                groups[av[0]] = text
            out.append(text)
        elif op == BRANCH:
            out.append(_gen(av[1][0], groups))
    return "".join(out)


def _literal_runs(sub, out):
    run = []

    def flush():
        if len(run) >= 2:
            out.add("".join(run))
        run.clear()
    for op, av in sub:
        if op == LITERAL:
            run.append(chr(av))
            continue
        if (op == CATEGORY and str(av).endswith("SPACE") or op == IN and _gen([(op, av)]) == " "
                or op in (MAX_REPEAT, MIN_REPEAT) and _gen(av[2]) == " "):
            run.append(" ")
            continue
        flush()
        if op in (MAX_REPEAT, MIN_REPEAT):
            _literal_runs(av[2], out)
        elif op == SUBPATTERN:
            _literal_runs(av[-1], out)
        elif op == BRANCH:
            for b in av[1]:
                _literal_runs(b, out)
        elif op in (ASSERT, ASSERT_NOT):
            _literal_runs(av[1], out)
    flush()
    return out


def _joined_runs(sub, out, head=""):
    """The literal text before a branch joined to each alternative's leading literals: the parser factors a shared
    prefix out of `gh\\s+(issue|pr)…|gh\\s+api…`, which split "gh api" into "gh" and " api"."""
    run = list(head)
    for op, av in sub:
        if op == LITERAL:
            run.append(chr(av))
        elif (op == IN and _gen([(op, av)]) == " ") or (op in (MAX_REPEAT, MIN_REPEAT) and _gen(av[2]) == " "):
            run.append(" ")
        elif op == BRANCH:
            for b in av[1]:
                _joined_runs(b, out, "".join(run))
            return out
        elif op == SUBPATTERN:
            _joined_runs(av[-1], out, "".join(run))
            run = []
        else:
            break
    if len(run) >= 2:
        out.add("".join(run))
    return out


def pumps(pattern, flags=0):
    """[(prefix, unit)]: each unit repeated after each prefix. The prefixes are none, the text the pattern's leading
    items match, and its whole sample, so a pattern anchored at `^` is pumped where its repeat is, not before it."""
    try:
        parsed = list(_sre_parse.parse(pattern, flags))
    except Exception:                                         # noqa: BLE001
        return [("", u) for u in SINGLES]
    units = set(SINGLES)
    sample = _gen(parsed)
    words = _literal_runs(parsed, set()) | _joined_runs(parsed, set())
    for w in words:                                          # each keyword, repeated alone or with a space
        units.update((w, w + " "))
    for sep in SEPARATORS:                                   # the whole sample, with every separator
        if sample:
            units.add(sample + sep)
    # The prefix is none, or the start of the pattern's own sample, so a pattern anchored at `^` (the extraction
    # patterns) is pumped after the text that reaches its repeat, not before it.
    prefixes = sorted({"", sample[:40]})
    return [(pre, u) for pre in prefixes for u in sorted(u for u in units if u)]


def _best(fn, s, reps=3):
    b = 1e9
    for _ in range(reps):
        t = time.perf_counter()
        fn(s)
        b = min(b, time.perf_counter() - t)
    return b


def attack(fn, units):
    """None when linear on every pump, else (prefix + pump, suffix, [(chars, seconds) at n, 2n, 4n])."""
    for pre, unit in units:
        for suf in SUFFIXES:
            # A LADDER, NOT ONE BIG CALL. A pattern that explodes (the extraction patterns: 885 s for one call at
            # 20,000 characters) is caught at a few hundred characters, in milliseconds, instead of after minutes.
            # Doubling while a call is under 0.2 ms, then an eighth at a time: an exponential pattern (the old frame
            # regex: 0.009 s at 238 characters, 139 s at 476) can never take more than a few seconds in one step.
            target = max(1, SCREEN_CHARS // len(unit))
            k, first = max(1, 250 // len(unit)), 0.0
            while True:
                first = _best(fn, pre + unit * k + suf, reps=1)
                if first > SLOW_SECONDS:                # no linear pattern takes half a second below 20,000 characters
                    return pre + unit, suf, [(len(unit) * k, first)], (pre, unit)
                if k >= target or first >= SCREEN_SECONDS:
                    break
                k = min(target, k * 2 if first < 0.0002 else k + max(1, k // 8))
            if first < SCREEN_SECONDS:
                continue
            # GROWTH over four steps of an eighth (60 % more input): linear grows the time about 1.6x, quadratic 2.6x.
            # Steps of an eighth until a call takes 20 ms or the input has grown four times, so the exponent is read
            # on timings well above the clock's noise.
            pts = [(len(unit) * k, _best(fn, pre + unit * k + suf))]
            k0 = k
            while pts[-1][1] < 0.02 and k < 4 * k0 or len(pts) < 5:
                k += max(1, k // 8)
                pts.append((len(unit) * k, _best(fn, pre + unit * k + suf)))
                if pts[-1][1] > SLOW_SECONDS:           # stop before a call grows into minutes
                    break
            (l0, t0), (l1, t1) = pts[0], pts[-1]
            expo = math.log(max(t1, 1e-7) / max(t0, 1e-7)) / math.log(l1 / l0)
            if expo > 1.6 and t1 > 0.01 and (t1 > SLOW_SECONDS or _confirmed(fn, pre, unit, suf, k, pts)):
                return pre + unit, suf, pts, (pre, unit)
    return None


#: The CONFIRMATION a growth verdict needs (3.21.1, CI run 39372338135). The ladder reads its exponent on calls of 1 to
#: 20 ms, where one scheduler stall decides the verdict: on a loaded CI runner `_secrets._ASSIGN` read 0.001 s at
#: 7,047 characters and 0.015 s at 28,863, while it is linear to 4 MB (0.026 s at 200 KB, 0.136 s at 1 MB, 0.556 s at 4 MB, the minimum of 3). So a flag
#: stands only if 4x the input takes more than CONFIRM_RATIO times as long. Linear takes about 4x, quadratic 16x.
#:
#: THE FLOOR IS ON THE SMALLER CALL, AND THE TWO SIDES ARE READ INTERLEAVED (3.21.1, stress run 1 under load). The
#: first confirmation put the floor on the larger call and read each side's minimum of 3 back to back. Under load
#: `core._INSTRUCTION_SHAPES`, which is linear, read 0.056 s at 164,088 characters against under 0.007 s at 41,022:
#: one load phase inflated all three larger readings past the floor, while the smaller side read clean. So the smaller
#: call grows by eighths until it alone takes CONFIRM_FLOOR (up to CONFIRM_CAP characters, where under 50 ms is
#: linear on its own), and the minimum of 3 is taken over small and large readings that alternate, so a load phase
#: falls on both sides. A doubling is read first: a pattern that is exponential never reaches the 4x call.
#:
#: THE DOUBLING IS READ IN EIGHTHS, AND NO CALL IS MORE THAN ONE EIGHTH-STEP PAST THE LAST (3.23.1, CI job
#: 119669956499). It used to be one call at 2k. The ladder flags the old frame guard through SLOW_SECONDS only when its
#: fifth growth point crosses 0.5 s (0.515 s at 340 characters on PC1, a 3% margin), so on a runner where that point
#: read under 0.5 s the pattern came here. The floor stopped at 306 characters (0.140 s), and the one call at 612
#: characters projects to 2.4e5 s at the x2.22 per repeat measured there. test (3.9) sat in that call until the 35-minute
#: session watchdog ended the run. Now each eighth-step is checked before the next one runs. A step that grows faster than
#: the 16x-for-2x rule allows, scaled to its own size ((m/k)**4), is the exponential verdict, confirmed on that step.
#: A step past SLOW_SECONDS ends the reading, and the 4x comparison is then made at that step.
#: MEASURED COST OF ONE EIGHTH-STEP at that growth rate: x4.98 (0.140 s at 306 characters to 0.697 s at 340,
#: OLD_FRAME, PC1 2026-10-26, agora_output/strategy/n322/redos_frame_probe.out). The first step runs from the floor
#: reading, which is under CONFIRM_FLOOR times one step, so the longest call this confirmation makes on that pattern is
#: about 0.05 x 4.98 x 4.98 = 1.2 s. A quadratic step costs x1.27 (1.125 squared). The 4x call runs only when every
#: step to 2k grew slower than the fourth power and 2k took under SLOW_SECONDS, so it costs at most 16 x 0.5 = 8 s.
CONFIRM_RATIO = 8.0
CONFIRM_FLOOR = 0.05
CONFIRM_CAP = 4000000


def _confirmed(fn, pre, unit, suf, k, pts):
    def text(m):
        return pre + unit * m + suf
    t, steps = _best(fn, text(k), reps=1), 0
    while t < CONFIRM_FLOOR and len(unit) * k < CONFIRM_CAP and steps < 64:
        k, steps = k + max(1, k // 8), steps + 1
        t = _best(fn, text(k), reps=1)
    if t < CONFIRM_FLOOR:
        return False                                          # CONFIRM_CAP characters in under 50 ms: linear
    m = k
    while m < 2 * k:                                          # the doubling, an eighth at a time
        m = min(2 * k, m + max(1, k // 8))
        t2 = _best(fn, text(m), reps=1)
        if t2 > (m / k) ** 4 * t:                             # faster than 16x for 2x: exponential; confirm it here
            t, t2 = min(t, _best(fn, text(k), reps=1)), min(t2, _best(fn, text(m), reps=1))
            pts.append((len(unit) * m, t2))
            return t2 > (m / k) ** 4 * t
        if t2 > SLOW_SECONDS:
            break
    # 4x the input against CONFIRM_RATIO, or, when a step of the doubling already passed SLOW_SECONDS, that step
    # against the same exponent (4 ** 1.5 == CONFIRM_RATIO), so no call is larger than one already read.
    top = 4 * k if t2 <= SLOW_SECONDS else m
    small, big = [], []
    for _ in range(3):                                        # interleaved: a load phase falls on both sides
        small.append(_best(fn, text(k), reps=1))
        big.append(_best(fn, text(top), reps=1))
    t_small, t_big = min(small), min(big)
    pts.extend([(len(unit) * k, t_small), (len(unit) * top, t_big)])
    return t_big > (top / k) ** math.log(CONFIRM_RATIO, 4) * t_small


class _FunctionStandIn:
    """A function that replaces a regex, with the regex it replaces as `.pattern`."""

    def __init__(self, fn, pattern):
        self.search, self.pattern = fn, pattern


def _function_stand_ins():
    from inspeximus import core
    return [("inspeximus.core._restore_intent", _FunctionStandIn(core._restore_intent, core._RESTORE_INTENT))]


def everything():
    """([(where, pattern, search callable, pump units)], [reference copies skipped], modules not imported).

    A literal equal to a stand-in's `.pattern` is the reference copy of the regex that stand-in replaced: it is kept
    for the record and for the equivalence tests, and no caller text reaches it, so it is listed, not attacked. The
    stand-in itself is attacked, with that pattern's pumps."""
    lits = literal_strings()
    lits.update({k: v for k, v in dynamic_sites()[1].items() if k not in lits})
    pats, objs, failed = runtime_objects()
    objs = objs + _function_stand_ins()
    references = {o.pattern for _w, o in objs}
    allp = dict(lits)
    for k, v in pats.items():
        allp.setdefault(k, v)
    rows, skipped = [], []
    for (pat, fl), where in sorted(allp.items(), key=lambda kv: kv[1]):
        if pat in references:
            skipped.append((where, pat))
            continue
        try:
            rx = re.compile(pat, fl)
        except re.error:
            continue
        rows.append((where, pat, rx.search, pumps(pat, fl)))
        if not fl:
            rows.append((where + " (re.I)", pat, re.compile(pat, re.I).search, None))
    for where, obj in objs:
        rows.append((where + " [stand-in]", obj.pattern, obj.search, pumps(obj.pattern, getattr(obj, "flags", 0))))
    return rows, skipped, failed


if __name__ == "__main__":
    rows, skipped, failed = everything()
    print("collected %d (patterns and stand-ins, each also with re.I where it had no flags); reference copies %d; "
          "modules not imported: %s" % (len(rows), len(skipped), failed or "none"))
    bad, t_all = 0, time.perf_counter()
    for i, (where, pat, fn, units) in enumerate(rows, 1):
        units = units or pumps(pat)
        t_row = time.perf_counter()
        r = attack(fn, units)
        if "-v" in sys.argv and time.perf_counter() - t_row > 1:
            print("  slow row %.1f s, %d pumps: %s %r" % (time.perf_counter() - t_row, len(units), where, pat[:60]), flush=True)
        if i % 100 == 0:
            print("  [%d/%d] %.0f s" % (i, len(rows), time.perf_counter() - t_all), flush=True)
        if r:
            bad += 1
            print("WORSE THAN LINEAR  %s  %r  pump %r%s  %s" % (where, pat[:80], r[0], " + suffix" if r[1] else "",
                                                                  [(c, round(t, 4)) for c, t in r[2]]))
    print("worse than linear:", bad)
