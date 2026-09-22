"""One intake form, one pack: everything an assessor asks for, with nothing silently blank.

WHY A PACK RATHER THAN SIX CALLS. Each document here already exists and each one asks the operator
for fields only they can write. Asked six times, in six shapes, an operator fills three of them, and
the gaps land in the PDF as "OPERATOR INPUT REQUIRED" where a reviewer finds them. `intake_form()`
asks once, and `assessor_pack()` refuses to call itself complete while anything is unanswered.

    form = intake_form()                      # every field, grouped, with where it is used
    pack = assessor_pack(store, ledger, operator=answers, expected_pubkey=pub)
    pack["complete"]                          # False while anything is unanswered
    pack["unfilled"]                          # each one named: field, document, why it is needed

WHAT IS IN THE PACK: the Annex IV technical documentation, the deployer report with its DPIA and
FRIA appendices, the Annex VIII registration export for sections A, B and C, the audit bundle, and
the agent audit trail. Every one is generated from the store and the ledger; nothing here invents
content, and the fields the operator did not answer are named rather than guessed.

WHAT IT IS NOT, and the pack says so in its own scope line: not a conformity assessment, not a
certification, and not a finding that any artifact satisfies an assessor. It is the evidence, laid
out in the order the Act asks for it.
"""
from __future__ import annotations

import json
import time

from . import __version__
from . import deployer as _deployer
from . import technical_documentation as _techdoc
from .technical_documentation import OPERATOR_INPUT

#: Where each group of operator fields is used, so the form can say why a field is being asked for.
FIELD_GROUPS = {
    "technical_documentation": {
        "fields": _techdoc.OPERATOR_FIELDS,
        "used_by": "Annex IV technical documentation (Art. 11)",
    },
    "deployer_report": {
        "fields": _deployer.DEPLOYER_FIELDS,
        "used_by": "deployer report (Art. 26)",
    },
    "dpia": {
        "fields": _deployer.DPIA_FIELDS,
        "used_by": "DPIA appendix (GDPR Art. 35)",
    },
    "fria": {
        "fields": _deployer.FRIA_FIELDS,
        "used_by": "FRIA appendix (AI Act Art. 27)",
    },
    "registration_a": {
        "fields": _techdoc.REGISTRATION_FIELDS["A"],
        "used_by": "Annex VIII section A, provider registration (Art. 49(1))",
    },
    "registration_b": {
        "fields": _techdoc.REGISTRATION_FIELDS["B"],
        "used_by": "Annex VIII section B, Art. 6(3) provider (Art. 49(2))",
    },
    "registration_c": {
        "fields": _techdoc.REGISTRATION_FIELDS["C"],
        "used_by": "Annex VIII section C, deployer (Art. 49(3))",
    },
}


def _trail(ledger, path, agent_id, agent_version) -> dict:
    """Write the IETF agent audit trail and report what it covered, with its own verification."""
    from .agent_audit_trail import export_jsonl, verify_jsonl
    out = export_jsonl(ledger, path, agent_id=agent_id, agent_version=agent_version)
    ok, problems = verify_jsonl(path)
    return {"path": str(path), "written": out, "verified": ok, "problems": problems}


def _names(fields) -> list:
    """The field names, whether the group is a list or a {field: annex point} mapping."""
    return list(fields.keys()) if isinstance(fields, dict) else list(fields)


def intake_form() -> dict:
    """Every field the pack needs from a person, once, grouped by what it feeds.

    A field used by more than one document is asked ONCE and listed with every place it lands, so an
    operator filling this in can see why a question matters rather than answering it twice.
    """
    where: dict = {}
    for group, spec in FIELD_GROUPS.items():
        for field in _names(spec["fields"]):
            where.setdefault(field, []).append(spec["used_by"])
    return {
        "kind": "inspeximus.assessor-intake/1",
        "fields": [{"field": f, "used_by": sorted(set(uses)), "value": ""} for f, uses in sorted(where.items())],
        "count": len(where),
        "how": ("Fill in `value` for each field and pass the result to assessor_pack(operator=...). "
                "An empty value is reported as unfilled and named in the pack; it is never left blank "
                "in a document."),
        "scope": ("These are the fields only the provider or the deployer can answer. Everything else "
                  "in the pack is generated from the store and the ledger."),
    }


def answers_from_form(form: dict) -> dict:
    """Turn a filled-in intake form back into the `operator` mapping the generators take."""
    out = {}
    for row in (form or {}).get("fields", []):
        value = row.get("value")
        if value not in (None, ""):
            out[row["field"]] = value
    return out


def _walk_for_placeholders(node, path="") -> list:
    """Every place in a rendered document where a field was left for the operator, with its path.

    Reading the RENDERED documents rather than the answers is the point: a generator that stops
    asking for a field, or starts asking for a new one, changes the pack, and a check against the
    field list would not notice. This one reads what an assessor would read.
    """
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _walk_for_placeholders(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _walk_for_placeholders(v, "%s[%d]" % (path, i))
    elif isinstance(node, str) and node.strip() == OPERATOR_INPUT:
        found.append(path)
    return found


def assessor_pack(store, ledger=None, operator: dict | None = None, expected_pubkey: str | None = None,
                  now: float | None = None, trail_path: str | None = None,
                  agent_id: str | None = None, agent_version: str | None = None) -> dict:
    """Render every document an assessor asks for, and name what is still missing.

    `complete` is True only when no document carries a placeholder AND no generator reported a
    missing field. The two are checked separately on purpose: the first reads the output, the second
    reads what each generator says about itself, and a disagreement between them is worth seeing.
    """
    operator = dict(operator or {})
    now = time.time() if now is None else now
    documents: dict = {}
    errors: dict = {}

    def build(name, fn):
        try:
            documents[name] = fn()
        except Exception as exc:                                 # noqa: BLE001 - a pack reports, never hides
            errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:200])

    build("technical_documentation",
          lambda: _techdoc.annex_iv(store, ledger, operator=operator,
                                    expected_pubkey=expected_pubkey))
    build("deployer_report",
          lambda: _deployer.deployer_report(store, ledger, operator=operator,
                                            expected_pubkey=expected_pubkey))
    for section in ("A", "B", "C"):
        build("registration_%s" % section.lower(),
              lambda s=section: _techdoc.registration_export(store, ledger, operator=operator,
                                                             section=s, expected_pubkey=expected_pubkey))
    from .audit_bundle import build_bundle
    build("audit_bundle", lambda: build_bundle(store, expected_pubkey=expected_pubkey))
    # The IETF trail is written to a FILE by design (it is JSONL a verifier streams), so the pack
    # records where it went and what it covered rather than inlining it. A pack that quietly
    # dropped it because the shape did not fit would be the worst of both.
    if trail_path and ledger is not None:
        build("agent_audit_trail",
              lambda: _trail(ledger, trail_path, agent_id or "inspeximus", agent_version or __version__))

    placeholders: list = []
    for name, doc in documents.items():
        placeholders += ["%s:%s" % (name, p) for p in _walk_for_placeholders(doc)]

    reported: dict = {}
    for name, doc in documents.items():
        missing = _collect_reported_missing(doc)
        if missing:
            reported[name] = sorted(set(missing))

    # EVERY place a field is used, not one of them. A dict comprehension over FIELD_GROUPS kept the
    # LAST group that declared each field, so a field used by three documents reported whichever
    # group happened to come last. Dogfooding the pack on our own store showed it: rows under
    # `registration_a` were labelled "Annex VIII section B", which is neither wrong data nor the
    # answer to the question the row is asking.
    known: dict = {}
    for spec in FIELD_GROUPS.values():
        for field in _names(spec["fields"]):
            known.setdefault(field, []).append(spec["used_by"])

    unfilled = []
    for name, fields in reported.items():
        for field in fields:
            unfilled.append({"field": field, "document": name,
                             "used_by": sorted(set(known.get(field) or ["this document"]))})

    return {
        "kind": "inspeximus.assessor-pack/1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "documents": documents,
        "errors": errors,
        "unfilled": unfilled,
        "placeholders_in_output": sorted(placeholders),
        "complete": not unfilled and not placeholders and not errors,
        "scope": ("Evidence, in the order the Act asks for it. Not a conformity assessment, not a "
                  "certification, and not a finding that any artifact satisfies an assessor. Fields "
                  "the operator did not answer are named here and marked in the documents; nothing "
                  "is guessed and nothing is left silently blank."),
    }


def _collect_reported_missing(node) -> list:
    """Whatever a generator said about its own missing fields, wherever it put it."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("operator_fields_missing", "missing_operator_fields") and isinstance(v, list):
                out += [str(x) for x in v]
            else:
                out += _collect_reported_missing(v)
    elif isinstance(node, list):
        for v in node:
            out += _collect_reported_missing(v)
    return out


def render_markdown(pack: dict) -> str:
    """The pack as one document a person reads, with the gaps at the top rather than buried."""
    lines = ["# Assessor pack", "", "Generated %s by inspeximus." % pack.get("generated_utc", ""), ""]
    if pack.get("complete"):
        lines += ["Every operator field is answered and no document carries a placeholder.", ""]
    else:
        lines += ["## Not complete", "",
                  "%d field(s) are unanswered, and each is marked in the document that needs it."
                  % len(pack.get("unfilled") or []), ""]
        for row in pack.get("unfilled") or []:
            lines.append("- `%s` for %s (%s)" % (row["field"], row["document"],
                                                       "; ".join(row["used_by"])))
        for path in pack.get("placeholders_in_output") or []:
            lines.append("- placeholder left in the output at `%s`" % path)
        for name, err in (pack.get("errors") or {}).items():
            lines.append("- %s could not be generated: %s" % (name, err))
        lines.append("")
    lines += ["## Documents", ""]
    for name in sorted(pack.get("documents") or {}):
        lines.append("### %s" % name.replace("_", " "))
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(pack["documents"][name], indent=2, sort_keys=True, default=str)[:20000])
        lines.append("```")
        lines.append("")
    lines += ["## Scope", "", pack.get("scope", ""), ""]
    return "\n".join(lines)
