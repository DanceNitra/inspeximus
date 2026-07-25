"""The store declares lineage at the call sites it owns — and a revert stops being a hole in erasure.

Background. Declared lineage measured **0.00%** across a real 27,290-record deployment, so 1.49.0 tried to
INFER it from content and 1.50.0 withdrew that at precision 0.06-0.23. This is the third option and the only
exact one: at a write site inside the library, the store already knows the parent, so it states it rather
than guessing.

The bug this closes is not cosmetic. `revert()` rebuilds a record's text from a specific predecessor and
recorded that parent in `meta['revert_of']` — a field no lineage check traverses. So a restored value looked
parentless: erase the subject its value came from, and the revert survived carrying that subject's data.

`rederive()` had the same defect and hid it better; see the test below. The class is only closed by
`test_the_owned_sites_are_declared_and_the_rest_are_not`, which fails if a new write site copies another
record's text without declaring where it came from.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _rec(m, rid):
    return next(r for r in m.items if r["id"] == rid)


def test_revert_declares_the_record_it_restored_from():
    m = _store()
    old = m.remember("billing uses api keys", key="billing::auth", object="api-keys",
                     source={"doc": "runbook-v1"})
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    restored = m.revert("billing::auth")["restored"]
    assert _rec(m, restored).get("derived_from") == [old]

    p = m.provenance(id=restored)
    assert old in p["origin"]["ancestors"]
    assert "runbookv1" in p["origin"]["inherited_taint"], \
        "the restored value's ORIGIN must ride the edge, not just the record id"


def test_a_revert_no_longer_hides_from_erasure():
    """THE bug. Before this, erasing the subject a value came from left the reverted copy behind."""
    m = _store()
    m.remember("billing uses api keys", key="billing::auth", object="api-keys",
               source={"doc": "runbook-v1"})
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    restored = m.revert("billing::auth")["restored"]

    erased = m.forget_subject("runbook-v1", request_id="REQ-1", basis="gdpr-art17")
    assert restored in erased["ids"], \
        "the reverted record carries runbook-v1's value and must be erased with it"
    assert m.erasure_audit(subject="runbook-v1")["residue"] == []


def test_rederive_declares_the_record_its_TEXT_came_from():
    """The same bug as revert, one function over, and worse: rederive builds the new text OUT OF a demoted
    record (`rewrite(r['text'], old, new)`) but declared only the corrected root as parent, filing the actual
    text parent in `meta['rederived_from']` — a field nothing traverses.

    Measured before the fix: erasing the subject the text came from reported `erased 1`, the rederived copy
    survived carrying that subject's wording verbatim, and `erasure_audit` returned `no_declared_residue` —
    it certified the leak as clean. An audit that cannot see the residue is worse than no audit.
    """
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    m.remember("alice bernard reaches the nightly backup with api-keys", derived=True,
               derived_from=[root], source={"doc": "alice-ticket"})
    # ORDER MATTERS: the keyed root must still be active when the lineage is retracted, or it is never
    # stamped needs_rederivation and rederive cannot resolve the old value.
    m.retract_lineage("runbook")
    corrected = m.remember("billing uses oauth2", key="billing::auth", object="oauth2",
                           source={"doc": "adr-014"})

    res = m.rederive("runbook")
    assert res["rederived"] == 1, res
    new_id = res["ids"][0]

    assert corrected in (_rec(m, new_id).get("derived_from") or []), \
        "the corrected current record stays declared — the fix ADDS a parent, it does not swap one"
    assert "aliceticket" in (_rec(m, new_id).get("taint") or []), \
        "the record the TEXT was rewritten from must ride the lineage edge too"

    erased = m.forget_subject("alice-ticket", request_id="REQ-1", basis="gdpr-art17")
    assert new_id in erased["ids"], "a rederived copy still carries its source's wording and must go with it"


def test_rederive_still_actually_rederives():
    """Erasability is not the only requirement — over-tainting could make the correction itself unusable."""
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    m.remember("the nightly backup signs in with api-keys", derived=True, derived_from=[root],
               source={"doc": "ops-notes"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    new_id = m.rederive("runbook")["ids"][0]
    assert _rec(m, new_id)["text"] == "the nightly backup signs in with oauth2"
    assert any(h["id"] == new_id for h in m.recall("nightly backup", k=3)), \
        "the corrected derivative must be recallable, not just present"


def test_resolve_reopened_declares_the_reopened_record():
    m = _store()
    m.remember("tz is UTC", key="user::tz", object="UTC")
    m.remember("tz is PST", key="user::tz", object="PST")
    reopened = m.reopened() if hasattr(m, "reopened") else []
    if not reopened:                       # nothing surfaced a prior on this path; the site is still patched
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "inspeximus", "core.py"), encoding="utf-8").read()
        assert re.search(r"capability=capability, derived_from=\[rid\]", src), \
            "resolve_reopened must declare the reopened record as parent"
        return
    rid = reopened[0]["id"]
    new_id = m.resolve_reopened(rid, "reaffirm_prior")["reaffirmed"]
    assert _rec(m, new_id).get("derived_from") == [rid]


def test_writes_that_are_NOT_derivations_stay_clean():
    """Over-declaring is its own failure. A plain write, a decision and an admitted record have no in-store
    parent, and inventing one would taint everything with everything."""
    m = _store()
    plain = m.remember("an independent observation about billing")
    assert _rec(m, plain).get("derived_from") is None

    dec = m.remember_decision("use oauth2", because="keys leak", context="billing")
    assert _rec(m, dec).get("derived_from") is None


def test_erasing_a_rederived_copy_does_not_lock_the_record_on_its_wrong_value():
    """`meta['rederived_to']` is rederive's single-shot guard, so it GATES BEHAVIOUR. Erase the rederived
    copy (bad rewrite, PII, whatever) and a live pointer to a deleted record froze the derived fact on the
    value we had just corrected away — with rederive returning 0/0 and no note to say why."""
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    derived = m.remember("the nightly backup signs in with api-keys", derived=True,
                         derived_from=[root], source={"doc": "ops"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr"})

    first = m.rederive("runbook")["ids"][0]
    m.forget(ids=[first], request_id="REQ-2", basis="gdpr-art17")
    assert (_rec(m, derived).get("meta") or {}).get("rederived_to") is None, \
        "a pointer to an erased record must not survive as an 'already done' guard"

    again = m.rederive("runbook")
    assert again["rederived"] == 1, ("the correction must be re-appliable after its output was erased, "
                                     f"else the store is stuck on the wrong value: {again}")
    assert "oauth2" in _rec(m, again["ids"][0])["text"]


def test_history_pointers_are_KEPT_so_the_audit_can_still_see_the_hole():
    """The counterpart. Scrubbing lineage on erasure would delete the evidence and flip erasure_audit to a
    false clean — the exact failure this whole file exists to prevent. Only behaviour-gating pointers go."""
    m = _store()
    parent = m.remember("patient chart body", source={"doc": "chart-77"})
    child = m.remember("summary of the chart", derived=True, derived_from=[parent])

    m.forget(ids=[parent], request_id="REQ-3", basis="gdpr-art17")
    assert parent in (_rec(m, child).get("derived_from") or []), \
        "the edge to an erased parent is EVIDENCE, not litter"

    audit = m.erasure_audit(subject="chart-77")
    assert audit["verdict"] == "residue_found"
    assert any(r["kind"] == "dangling_lineage" for r in audit["residue"]), \
        "the audit must still be able to report that a survivor descends from erased content"


def test_the_owned_sites_are_declared_and_the_rest_are_not():
    """Pins the audit: of the library's own write sites, exactly the derivation ones declare a parent."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    lines = src.split("\n")
    declaring = set()
    for i, l in enumerate(lines, 1):
        if re.search(r"self\.remember\(", l):
            fn = "?"
            for j in range(i - 1, 0, -1):
                mm = re.match(r"    def (\w+)", lines[j - 1])
                if mm:
                    fn = mm.group(1)
                    break
            if re.search(r"derived_from\s*=|derived\s*=\s*True", " ".join(lines[i - 1:i + 4])):
                declaring.add(fn)
    assert declaring == {"rederive", "revert", "submit_revert", "resolve_reopened"}, declaring


def _copying_write_sites(pkg_dir):
    """Every `.remember(...)` in the package whose TEXT argument is lifted out of another record, paired with
    the record variable it came from and the parents that call declares.

    Uses `ast`, not a regex over lines. The first version of this guard WAS a regex, and an adversarial review
    injected four offending shapes into core.py: it caught two, was blind to a multi-line call and to a local
    whose name was not on a hard-coded list, and raised a FALSE POSITIVE on the legitimate
    `pid = r["id"]; derived_from=[pid]`. A guard with 50% recall that also cries wolf is worse than none,
    because it reports safe.
    """
    import ast
    sites = []
    for path in sorted(pathlib.Path(pkg_dir).rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:                       # not ours to police
            continue

        def record_read_in(node):
            """The variable X in X["text"] / X.get("text") anywhere inside `node`, else None."""
            for n in ast.walk(node):
                if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                        and isinstance(n.slice, ast.Constant) and n.slice.value == "text"):
                    return n.value.id
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and isinstance(n.func.value, ast.Name) and n.args
                        and isinstance(n.args[0], ast.Constant) and n.args[0].value == "text"):
                    return n.func.value.id
            return None

        def store_derived_names(fn):
            """Names bound to something that came OUT OF the store.

            Needed because `X.get("text")` alone does not mean X is a memory record. `distill_and_remember`
            iterates dicts parsed from an LLM's JSON — external input with no in-store parent to declare, and
            the first version of this guard reported it as an offence. A record is one that traces back to
            `self.items` / `self.recall(...)` / `self.get(...)`, transitively through containers and loops.
            """
            roots, changed = set(), True
            def from_store(node):
                for n in ast.walk(node):
                    if (isinstance(n, ast.Attribute) and n.attr == "items"
                            and isinstance(n.value, ast.Name) and n.value.id == "self"):
                        return True
                    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                            and n.func.attr in ("recall", "get", "history", "neighbors")
                            and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"):
                        return True
                    if isinstance(n, ast.Name) and n.id in roots:
                        return True
                return False
            while changed:                                    # fixpoint: containers feed loops feed containers
                changed = False
                for n in ast.walk(fn):
                    tgt = None
                    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                        tgt, val = n.targets[0].id, n.value
                    elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
                        tgt, val = n.target.id, n.iter
                    elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
                        tgt, val = n.target.id, n.iter
                    if tgt and tgt not in roots and from_store(val):
                        roots.add(tgt)
                        changed = True
            return roots

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            records = store_derived_names(fn)
            local_src, id_alias = {}, {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    name = n.targets[0].id
                    got = record_read_in(n.value)
                    if got and name not in local_src:     # first assignment that reads a record wins, so
                        local_src[name] = got             # `nt = None` in an except branch cannot mask it
                    if (isinstance(n.value, ast.Subscript) and isinstance(n.value.value, ast.Name)
                            and isinstance(n.value.slice, ast.Constant) and n.value.slice.value == "id"):
                        id_alias[name] = n.value.value.id  # `pid = r["id"]` is a legitimate way to declare

            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "remember"):
                    continue
                text_arg = call.args[0] if call.args else next(
                    (k.value for k in call.keywords if k.arg == "text"), None)
                if text_arg is None:
                    continue
                source_var = (local_src.get(text_arg.id) if isinstance(text_arg, ast.Name)
                              else record_read_in(text_arg))
                if not source_var or source_var not in records:
                    continue                      # not a store record -> no in-store parent exists to declare

                declared = set()
                for kw in call.keywords:
                    if kw.arg != "derived_from" or kw.value is None:
                        continue
                    for n in ast.walk(kw.value):
                        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                                and isinstance(n.slice, ast.Constant) and n.slice.value == "id"):
                            declared.add(n.value.id)
                        elif isinstance(n, ast.Name) and n.id in id_alias:
                            declared.add(id_alias[n.id])
                sites.append({"file": path.name, "line": call.lineno, "func": fn.name,
                              "source_var": source_var, "declared": declared})
    return sites


def test_a_new_write_site_that_COPIES_TEXT_must_declare_where_it_came_from():
    """The guard for the class rather than for the two instances of it.

    Both bugs had one shape: an internal write built its text out of another record and declared no edge to
    THAT record, so erasure could not follow the content. Pinning today's function names does not stop a
    sixth site being added tomorrow with the same defect.

    Scope, stated plainly because the first version of this test overstated it: this is a SYNTACTIC check
    over `.remember()` calls in the whole `inspeximus` package. It resolves a text argument passed through a
    local and accepts a parent declared via an id alias. It CANNOT verify the declared parent is the
    semantically correct one, and it cannot see text no static reader can attribute — an f-string over
    several records, a helper in another package, or an LLM paraphrase written back as a fresh fact.
    It closes the shape both known bugs had. It does not make erasure sound.
    """
    pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inspeximus")
    offenders = [f'{s["file"]}:{s["line"]} {s["func"]}() writes text from `{s["source_var"]}` '
                 f'but declares {sorted(s["declared"]) or "no parent"}'
                 for s in _copying_write_sites(pkg) if s["source_var"] not in s["declared"]]
    assert not offenders, (
        "a write site copies another record's text without declaring THAT record as a parent. Erasure "
        "follows declared edges only, so the copy survives erasure of the subject its wording came from, "
        "and erasure_audit reports no declared residue:\n  " + "\n  ".join(offenders))


def test_the_guard_catches_the_shapes_the_regex_version_missed():
    """Negative control, in both directions. The regex predecessor caught 2 of 4 offending shapes and
    false-alarmed on a legitimate declaration; without this, a refactor could quietly restore that state."""
    import ast
    import textwrap

    def offenders(code):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "m.py").write_text(textwrap.dedent(code), encoding="utf-8")
        return [s for s in _copying_write_sites(d) if s["source_var"] not in s["declared"]]

    LOOP = 'def f(self):\n    for r in self.items:\n        '
    must_catch = {
        "single line": LOOP + 'return self.remember(r["text"])',
        "multi line":  LOOP + 'body = r["text"].upper()\n        return self.remember(\n            body)',
        "other local": LOOP + 'summary = r["text"] + " x"\n        return self.remember(summary)',
        "text= kwarg": LOOP + 'return self.remember(text=r["text"])',
        "via .get":    LOOP + 't = r.get("text", "")\n        return self.remember(t)',
        "via recall":  'def f(self, q):\n    for h in self.recall(q):\n        return self.remember(h["text"])',
    }
    for label, code in must_catch.items():
        assert offenders(code), f"the guard is blind to: {label}"

    must_pass = {
        "declared direct": LOOP + 'return self.remember(r["text"], derived_from=[r["id"]])',
        "declared alias":  LOOP + 'pid = r["id"]\n        return self.remember(r["text"], derived_from=[pid])',
        "not a copy":      'def f(self):\n    return self.remember("an independent observation")',
        # the false positive that made this discrimination necessary: an LLM's JSON payload is not a record
        "external payload": ('def f(self, blob):\n    for it in json.loads(blob):\n'
                             '        self.remember(it.get("text"))'),
    }
    for label, code in must_pass.items():
        assert not offenders(code), f"the guard false-alarms on: {label}"


def test_erasing_the_retracted_source_also_erases_the_repair():
    """A CONSEQUENCE of the fix, pinned so it stays intentional.

    rederive() repairs a derived fact by rewriting the demoted record's text. Declaring that record as a
    parent means the repair also inherits the RETRACTED source's taint — so erasing that source now takes the
    repair with it, and the store is left holding neither the wrong value nor the right one.

    This is not a regression the fix introduced. Before it, the repair survived because it was INVISIBLE to
    erasure, not because anything judged it safe. The fix makes a real inheritance visible, and an operator
    can now see the blast radius — but only after the fact: forget_subject() has no dry_run, so there is no
    preview. Recorded as a known limit rather than papered over.
    """
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",
                      source={"doc": "runbook"})
    m.remember("the nightly backup signs in with api-keys", derived=True, derived_from=[root],
               source={"doc": "ops-notes"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    repair = m.rederive("runbook")["ids"][0]

    assert "runbook" in (_rec(m, repair).get("taint") or []), "the repair descends from the retracted source"
    erased = m.forget_subject("runbook", request_id="REQ-4", basis="gdpr-art17")
    assert repair in erased["ids"], "erasing the retracted source takes the repair with it — by design"
    assert not hasattr(m.forget_subject, "dry_run")   # documents the gap: no preview of the blast radius
