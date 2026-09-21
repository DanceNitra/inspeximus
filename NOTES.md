# inspeximus 3.4.0

GDPR Art. 13/14, 20, 21 and 28 as evidence, so every in-scope duty in the matrix is covered; an objection that withholds the subject from recall; the cluster pass at 2 s instead of 300; a lineage rewrite that lands. UPGRADE IF YOU RUN sleep() ON A STORE ABOVE A FEW HUNDRED RECORDS, OR SERVE DATA-SUBJECT REQUESTS. AFFECTS: adds `object_processing`, `resolve_objection` and `objections` on the store, `record_objection`, `resolve_objection` and a `basis="portability"` export in `subject_rights`, `record_notice`, `notice_register`, `record_processing_role` and `processing_roles` on the ledger, seven MCP tools (128), CLI subcommands under `subject` and `actions`, two new entry kinds, `notice` and `processing_role`, and one new sidecar, `<store>.objections.json`, written only once an objection exists; `_cluster_active` is rewritten with the same output; the objectless guard lets a verbatim restatement through and reports what it retires. The default store is otherwise byte-identical.

## Who should upgrade

Upgrade if this is true of you: **YOU RUN sleep() ON A STORE ABOVE A FEW HUNDRED RECORDS, OR SERVE DATA-SUBJECT REQUESTS. AFFECTS**.

## What changed

`object_processing(subject, actor, ground)` records a GDPR Art. 21 objection on the store and, from
that call on, recall withholds every record whose source resolves to the subject, including records
written later, on the STORE rather than through a caller argument, so no omitted parameter serves
them. The subject is resolved the way erasure resolves it, path and all: the first version filtered
by canonical host and the control refuted it in one run, because `crm/alice` and `crm/bob` share
the host and Alice's objection withheld Bob. `ground` is `own_situation` (21(1)) or `direct_marketing`
(21(2)). `resolve_objection` closes it as `upheld` (still withheld; the controller's next step is
erasure or restriction) or `overridden` (21(1) compelling legitimate grounds, which must be stated,
and which a direct-marketing objection is refused). The records stay exportable under Art. 15. Both
calls have `rights:objection` and `rights:objection_resolved` ledger forms in `subject_rights`.

`export_subject(basis="portability")` is the Art. 20 response: `response_to`, a versioned `format`
(`inspeximus.subject_export` version 1, JSON, UTF-8), a `portable` flag per record (a direct record
the subject provided, as against one derived from it), and a `rights:portability` ledger entry. The
Art. 15 export is unchanged.

`record_notice()` records an Art. 13 (data collected from the subject) or Art. 14 (obtained
elsewhere) notice: channel, the items given from the article's list, the items it did NOT carry as
`missing`, a hash of the text, and for Art. 14 the source and the 14(3) timing. `notice_register()`
shows the latest per subject and the incomplete ones. `record_processing_role()` records the
operator's Art. 28 role: a processor names its controller and the 28(3) written instructions, and
each sub-processor its 28(2) authorisation. `processing_roles()` names the current declaration.

`inspeximus coverage` on a fresh store: 36 of 36 in-scope duties covered, 0 not covered (was 33
and 3). Every row of the matrix in docs/EVIDENCE_PLATFORM_PLAN.md now has an artifact.

`_cluster_active()`, which `sleep()` and `consolidate_clusters()` run, took 305 to 378 s on a
3,343-record store (Crew OS, 2026-09-21) and blocked the caller for the whole time. Two causes, both
measured: the centroid text was re-tokenised on every comparison (5.6 million tokenisations for
3,343 records in lexical mode), and every cluster was scored even when it could not reach the
threshold. Tokens are now cached per record and passed as `qtok` at the five sites that dropped it,
and an inverted index scores only clusters that share enough tokens to pass, an exact bound from
the overlap coefficient. Measured on a copy of that store
(probes/the_cluster_pass_is_quadratic_and_the_fix_is_exact.py): 10.4 / 39.0 / 162.5 s to 0.08 /
0.46 / 0.92 s on prefixes of 500 / 1,000 / 2,000 records, identical clusters on every prefix, and
sleep() on the whole store at 2.0 s. Semantic mode (cosine) scores every cluster as before.

The objectless clobber guard refused a keyed write that repeated the current text verbatim and
added only `derived_from`, because the key carried an `object` and the write did not; lineage could
not be added to an existing record without changing its text (Crew OS, same day). The same text
cannot displace a value, so a verbatim restatement now inherits the incumbent's object and goes
through supersession. Found while reproducing: the guard returned the write as retired while
`last_write` said active, blocked False; it now reports `policy: objectless_guard` like the echo guard.

Two gate fixes. `release_check` has a `no empty module` leg, because a patch script emptied
`inspeximus/actions.py` before raising on its own argument and the only symptom was an ImportError
one level removed. The published-commands test ignores probe scratch files when it copies `probes/`,
because a SQLite journal another xdist worker was writing vanished mid-copy on the 3.3.0 release run.

Eighteen mutations, all killed. Coverage matrix, MCP listing and tool count regenerated.

## What breaks

No line in the 3.4.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.4.0"
```

No server, no API key, no database, no LLM on the write path. A correction, and the retired value
staying retired:

```python
from inspeximus import Inspeximus, regex_extractor

m = Inspeximus(path="demo.json")
m.extractor = regex_extractor                            # deterministic subject/predicate keys
m.remember("The staging database is db-1.internal.")
m.remember("The staging database is db-7.internal.")     # a correction, not a second fact
print([h["text"] for h in m.recall("staging database", k=3)])
# -> ['The staging database is db-7.internal.']
```

For the MCP server (the one part that has a dependency): `pip install -U "inspeximus[mcp]"` and point
your client at `inspeximus-mcp`. In Claude Code: `/plugin marketplace add DanceNitra/inspeximus` then
`/plugin install inspeximus@inspeximus`.

## Check it yourself

Nothing above asks you to take our word for it:

- `pip download inspeximus` -- PyPI records a signed attestation binding the wheel to this repository,
  this workflow and this commit. Built by GitHub OIDC; no API token exists anywhere.
- `python -m pytest tests/ -q` in a clone -- the suite that had to be green before this was tagged.
- `python tools/release_check.py` -- the pre-release checklist itself, including the example above.
- `inspeximus residue --root ./your-deployment --value <a value you deleted>` -- points at ANY store,
  not just ours, and exits non-zero if the bytes are still there.

Full detail, including what we got wrong and had to correct: [CHANGELOG.md](
https://github.com/DanceNitra/inspeximus/blob/main/CHANGELOG.md).
