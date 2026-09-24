"""MCP tool review, family: MAINTENANCE, CONFLICT AND STORE-ANALYSIS TOOLS.

Part of audits/2026-09-24/mcp-tools-review.md. Tools: consolidate, sleep, consolidate_clusters,
contradictions, check_conflict, verify_claim, check_self_narration, selection_integrity, value_by_cohort,
memory_report, index_coherence, identifier_contract, check_sources, influence_gate_report,
irreversible_budget_report, credit, memory_index, set_index_line.

Each test holds one tool to a sentence of its OWN description (the docstring an MCP client is shown) or
to the server's documented configuration, and fails today. They are strict xfails: the day the tool is
fixed, the test XPASSes and the marker has to come off. Preconditions go through `pytest.fail`, so a
setup that did not do what the test needs fails loudly instead of passing as an expected failure.
Every test runs on a throwaway store in tmp_path (see tests/_mcp_review.py).

"Reaches the store file" is checked by opening a SECOND handle on the same path, which is what the next
server process, the Claude Code hook or a peer server sees. The server never flushes at exit (there is no
flush()/atexit anywhere in mcp_server.py), so a change that is only in this handle's memory when the
process ends is gone. `_MEM._last_save` is pinned where a test depends on the library's 5-second save
throttle, so the outcome does not depend on how fast the machine runs the previous call.
"""
import time

import pytest

pytest.importorskip("mcp")

from _mcp_review import call, load_server  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402

XFAIL = dict(strict=True, raises=AssertionError)


def _record(mod, rid):
    return next((r for r in mod._MEM.items if r.get("id") == rid), None)


def _on_disk(tmp_path):
    """The records a fresh process would load from the store file."""
    return {r["id"]: r for r in Inspeximus(path=str(tmp_path / "store.json")).items}


def _just_saved(mod):
    """Pin the save throttle: the previous save happened now, as it does after any write."""
    mod._MEM._last_save = time.time()


# ── credit ──────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="credit: 'each memory's track record updates ... Counts only grow'; the update is "
                          "only in memory (unforced, throttled _save) and is lost when the server exits", **XFAIL)
def test_credit_reaches_the_store_file(monkeypatch, tmp_path):
    """credit: "call credit(those ids, outcome) so each memory's track record updates. Future `recall` then
    ranks by WAS-IT-RIGHT ... Counts only grow; raw text is never edited. Returns what updated."

    The tool returns `updated: [id]`, but core.credit ends in an unforced `_save()`, which within 5 s of the
    previous save only sets `_dirty`. The next process opens the store without the credit.
    """
    mod = load_server(monkeypatch, tmp_path)
    rid = call(mod, "remember", text="the build server is jenkins-7").data["id"]
    _just_saved(mod)
    res = call(mod, "credit", ids=[rid], outcome="good")
    if res.is_error or res.data.get("updated") != [rid] or (_record(mod, rid) or {}).get("good") != 1.0:
        pytest.fail(f"precondition: the credit must land in this handle, got {res}")
    assert _on_disk(tmp_path)[rid].get("good") == 1.0, \
        "credit reported the record as updated, and a fresh open of the store file has no credit on it"


@pytest.mark.xfail(reason="credit: '(or pass a bool / a signed number)'; `outcome: str` rejects a JSON number "
                          "and the string form of a positive number is recorded as outcome 'bad'", **XFAIL)
def test_credit_accepts_a_positive_signed_number_as_a_good_outcome(monkeypatch, tmp_path):
    """credit: "`outcome`: 'good'/'right'/'correct' vs 'bad'/'wrong'/'failed' (or pass a bool / a signed
    number)."

    The tool's schema is `outcome: string`, so a JSON number is refused by argument validation, and the
    only form a client can send, "+1", falls through the verdict-word list in core._outcome_good and is
    written as a FAILURE: the memory's `bad` count goes up.
    """
    mod = load_server(monkeypatch, tmp_path)
    a = call(mod, "remember", text="the cache ttl is three hundred seconds").data["id"]
    b = call(mod, "remember", text="the retry budget is five attempts").data["id"]
    as_number = call(mod, "credit", ids=[a], outcome=1)
    as_text = call(mod, "credit", ids=[b], outcome="+1")
    if as_text.is_error or as_text.data.get("updated") != [b]:
        pytest.fail(f"precondition: the string form must reach the store, got {as_text}")
    number_ok = (not as_number.is_error) and as_number.data.get("outcome") == "good"
    text_ok = as_text.data.get("outcome") == "good" and (_record(mod, b) or {}).get("good") == 1.0
    assert number_ok or text_ok, (
        f"a positive signed number is not a good outcome on this surface: JSON 1 -> "
        f"{'isError' if as_number.is_error else as_number.data}; '+1' -> {as_text.data}, "
        f"record counts {{good: {_record(mod, b).get('good')}, bad: {_record(mod, b).get('bad')}}}")


@pytest.mark.xfail(reason="credit: 'Counts only grow'; a negative `weight` is added as-is and shrinks "
                          "the good/bad counts", **XFAIL)
def test_credit_counts_only_grow(monkeypatch, tmp_path):
    """credit: "Counts only grow; raw text is never edited."

    `weight` is added to the count unchecked (core.credit: `rec[key] = ... + float(weight)`), so a negative
    weight takes back recorded outcomes -- including a record's recorded FAILURES, which the influence gate
    reads.
    """
    mod = load_server(monkeypatch, tmp_path)
    rid = call(mod, "remember", text="the staging database is postgres fifteen").data["id"]
    first = call(mod, "credit", ids=[rid], outcome="bad", weight=2.0)
    if first.is_error or (_record(mod, rid) or {}).get("bad") != 2.0:
        pytest.fail(f"precondition: a bad outcome must be recorded first, got {first}")
    res = call(mod, "credit", ids=[rid], outcome="bad", weight=-5.0)
    if res.is_error:
        return      # refusing a negative weight is one way to keep the promise
    assert (_record(mod, rid) or {}).get("bad", 0) >= 2.0, \
        f"credit accepted weight=-5 ({res.data}) and the record's bad count fell to {_record(mod, rid).get('bad')}"


# ── consolidate / consolidate_clusters / sleep ──────────────────────────────────────────────────────
@pytest.mark.xfail(reason="consolidate: 'if keep is given, supersede the lowest-value surplus'; the "
                          "supersessions stay in memory (unforced, throttled _save) and never reach the file", **XFAIL)
def test_consolidate_keep_budget_reaches_the_store_file(monkeypatch, tmp_path):
    """consolidate: "Run the consolidation 'dream' pass over ALL memories: ... and (if `keep` is given)
    supersede the lowest-value surplus. ... Returns a report (active / hubs_flagged / linked_pairs /
    toggled / ...)."

    The report says `active: 2`; core.consolidate ends in an unforced `_save()`, so a fresh open of the
    file still has all five records active.
    """
    mod = load_server(monkeypatch, tmp_path)
    texts = ["the alpha project uses postgres", "the beta project uses redis", "the gamma team prefers tabs",
             "the delta service runs in frankfurt", "the epsilon owner is maria"]
    ids = [call(mod, "remember", text=t, value=1.0 + i).data["id"] for i, t in enumerate(texts)]
    _just_saved(mod)
    res = call(mod, "consolidate", keep=2)
    live = [i for i in ids if (_record(mod, i) or {}).get("status") == "active"]
    if res.is_error or res.data.get("active") != 2 or len(live) != 2:
        pytest.fail(f"precondition: the keep-budget must supersede three records in this handle, got {res}")
    disk = _on_disk(tmp_path)
    still_active = [i for i in ids if disk[i]["status"] == "active"]
    assert still_active == live, \
        f"consolidate reported active=2; the store file still holds {len(still_active)} active records"


@pytest.mark.xfail(reason="consolidate_clusters: consolidates a ripe cluster (state-toggle); the "
                          "supersession stays in memory (unforced, throttled _save) and never reaches the file", **XFAIL)
def test_consolidate_clusters_reaches_the_store_file(monkeypatch, tmp_path):
    """consolidate_clusters: "Cluster-TRIGGERED consolidation: consolidate a semantic cluster only once it
    has grown past `threshold` members ... Returns clusters_total / clusters_fired / linked_pairs / ..."

    A two-member cluster that is a preference flip is toggled (`toggled: 1`, the older record superseded in
    this handle); core.consolidate_clusters ends in an unforced `_save()`, so the file keeps it active.
    """
    mod = load_server(monkeypatch, tmp_path)
    old = call(mod, "remember", text="the user likes coffee").data["id"]
    call(mod, "remember", text="the user does not like coffee")
    _just_saved(mod)
    res = call(mod, "consolidate_clusters", threshold=2)
    if res.is_error or res.data.get("toggled") != 1 or (_record(mod, old) or {}).get("status") != "superseded":
        pytest.fail(f"precondition: the ripe cluster must toggle the older record in this handle, got {res}")
    assert _on_disk(tmp_path)[old]["status"] == "superseded", \
        "consolidate_clusters reported toggled=1; the store file still has the flipped preference active"


@pytest.mark.xfail(reason="sleep: 'if keep is given ... prunes/re-affirms the memory budget'; the keep-budget "
                          "pass is never saved in the same call (consolidate_clusters' save consumes the "
                          "throttle window)", **XFAIL)
def test_sleep_keep_budget_reaches_the_store_file(monkeypatch, tmp_path):
    """sleep: "It consolidates any ripe near-duplicate clusters (dedup + preference-flip handling), and, if
    `keep` is given (or a capacity was configured), prunes/re-affirms the memory budget."

    core.sleep runs consolidate_clusters (which saves, resetting the 5 s throttle clock) and THEN
    consolidate(keep) (whose unforced save is therefore always throttled). So even with the throttle long
    expired before the call, the pruning never reaches the file in the call that reports it.
    """
    mod = load_server(monkeypatch, tmp_path)
    texts = ["the alpha project uses postgres", "the beta project uses redis", "the gamma team prefers tabs",
             "the delta service runs in frankfurt", "the epsilon owner is maria"]
    ids = [call(mod, "remember", text=t, value=1.0 + i).data["id"] for i, t in enumerate(texts)]
    mod._MEM._last_save = 0.0                   # the throttle is NOT what stands in the way here
    res = call(mod, "sleep", keep=2)
    live = [i for i in ids if (_record(mod, i) or {}).get("status") == "active"]
    if res.is_error or (res.data.get("keep_budget") or {}).get("active") != 2 or len(live) != 2:
        pytest.fail(f"precondition: sleep(keep=2) must prune to two records in this handle, got {res}")
    disk = _on_disk(tmp_path)
    still_active = [i for i in ids if disk[i]["status"] == "active"]
    assert still_active == live, \
        f"sleep reported keep_budget.active=2; the store file still holds {len(still_active)} active records"


# ── check_sources ───────────────────────────────────────────────────────────────────────────────────
def test_check_sources_is_scoped_to_the_servers_project(monkeypatch, tmp_path):
    """check_sources: "Scoped to the bound tenant/project when there is one."

    The server's project scope is passed per call to remember/recall and never bound to the store, and
    core.check_sources walks every record (`for r in self.items`). A server running as project `a` reports
    project `b`'s drifted record by id and turns `ok` false over it, while its own recall does not see it.
    """
    doc_b = tmp_path / "b_notes.md"
    doc_b.write_text("project b: the database is postgres 14\n")
    doc_a = tmp_path / "a_notes.md"
    doc_a.write_text("project a: the cache is redis\n")
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="b")
    rb = call(mod, "remember", text="the database is postgres 14", source=str(doc_b)).data
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="a")
    ra = call(mod, "remember", text="the cache is redis", source=str(doc_a)).data
    if rb.get("project") != "b" or ra.get("project") != "a":
        pytest.fail(f"precondition: the two records must be stamped b and a, got {rb} / {ra}")
    doc_b.write_text("project b: the database is postgres 16\n")
    seen = [h["id"] for h in call(mod, "recall", query="database postgres cache redis", k=5).data]
    if rb["id"] in seen or ra["id"] not in seen:
        pytest.fail(f"precondition: recall on project a must see a's record and not b's, got {seen}")
    rep = call(mod, "check_sources").data
    assert rb["id"] not in (rep.get("drifted") or []) and rep.get("checked") == 1, \
        f"a server scoped to project a reports project b's record: drifted={rep.get('drifted')}, " \
        f"checked={rep.get('checked')}, ok={rep.get('ok')}"


@pytest.mark.xfail(reason="check_sources: '`ok` is false whenever NOTHING was checkable'; a store whose "
                          "records carry no source reports checked=0 and ok=true", **XFAIL)
def test_check_sources_ok_is_false_when_nothing_was_checked(monkeypatch, tmp_path):
    """check_sources: "UNCHECKABLE (no fingerprint: no source, or a source naming the WRITER rather than a
    document). ... `ok` is false whenever NOTHING was checkable, and the report says so -- zero drifted over
    zero checked is the same sentence as a clean store."

    The library now files a record with no source as NOT_BINDABLE (a bucket the description does not name)
    and returns ok=True when every record is NOT_BINDABLE, beside a `problem` saying it verified nothing.
    """
    mod = load_server(monkeypatch, tmp_path)
    call(mod, "remember", text="the region is frankfurt")
    call(mod, "remember", text="the retry budget is five attempts")
    rep = call(mod, "check_sources").data
    if rep.get("checked") != 0:
        pytest.fail(f"precondition: nothing in this store can be checked, got {rep}")
    assert rep.get("ok") is False, \
        f"zero records checked and check_sources says ok={rep.get('ok')} (counts={rep.get('counts')})"


# ── verify_claim ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="verify_claim: \"'stale_superseded' ... 'current' is the truth now\"; a claim "
                          "passed without `key` comes back stale_superseded with current=None", **XFAIL)
def test_verify_claim_stale_superseded_names_the_current_value(monkeypatch, tmp_path):
    """verify_claim: "'stale_superseded' (matches a value that has since been CORRECTED/reverted -- the reply
    is citing an outdated fact; 'current' is the truth now)."

    The keyless path finds the retired record and returns `_out("stale_superseded", None, ...)`, although
    the retired record's key has an active value with an `object`. The keyed path returns it.
    """
    mod = load_server(monkeypatch, tmp_path)
    call(mod, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    call(mod, "remember", text="the region is osaka", key="svc::region", object="osaka")
    keyed = call(mod, "verify_claim", text="the region is frankfurt", key="svc::region", object="frankfurt").data
    if keyed.get("verdict") != "stale_superseded" or keyed.get("current") != "osaka":
        pytest.fail(f"precondition: the keyed claim must be stale with current=osaka, got {keyed}")
    res = call(mod, "verify_claim", text="the region is frankfurt").data
    if res.get("verdict") != "stale_superseded":
        pytest.fail(f"precondition: the keyless claim must be recognised as stale, got {res}")
    assert res.get("current") == "osaka", \
        f"verify_claim says the claim is stale and gives current={res.get('current')!r}; the truth now is 'osaka'"


# ── memory_report / selection_integrity: the recall-window observation ──────────────────────────────
def _served_then(mod, tool, **args):
    """recall, run `tool`, write; return (ids the client was served, the write's recall_window).

    A control write straight after a recall comes first, so a store that does not stamp windows at all
    fails the precondition instead of passing the test."""
    for t in ("the region is frankfurt", "the user likes coffee", "the deploy limit is one hundred",
              "the team standup is at nine"):
        call(mod, "remember", text=t)
    control = [h["id"] for h in call(mod, "recall", query="region frankfurt", k=1).data]
    cid = call(mod, "remember", text="the region is still frankfurt this week").data["id"]
    cwin = (_record(mod, cid) or {}).get("recall_window")
    if not control or not cwin or cwin.get("ids") != control:
        pytest.fail(f"precondition: a write straight after a recall must carry that recall as its window, "
                    f"got served={control} window={cwin}")
    served = [h["id"] for h in call(mod, "recall", query="the deploy limit", k=1).data]
    res = call(mod, tool, **args)
    if res.is_error or not served:
        pytest.fail(f"precondition: a recall must be served and {tool} must answer, got {served} / {res.text}")
    wid = call(mod, "remember", text="the deploy limit moved to two hundred").data["id"]
    return served, (_record(mod, wid) or {}).get("recall_window")


@pytest.mark.xfail(reason="memory_report: 'Read-only'; with INSPEXIMUS_OBSERVE_RECALL=1 its internal recalls "
                          "replace the window, so the next write records ids the client was never served", **XFAIL)
def test_memory_report_leaves_the_recall_window_alone(monkeypatch, tmp_path):
    """memory_report: "The at-a-glance store-health view. Read-only." Server config: "INSPEXIMUS_OBSERVE_RECALL
    record which memories were served immediately before each write, as an observation (`recall_window`)".

    core.memory_report runs `self.recall(r["text"], k=2)` per sampled record with the default observe=True,
    so the window the next write stamps is the last sampled record's neighbourhood, not the recall the
    client made. The library has `observe=False` for exactly this kind of maintenance read (which
    invalidates the window rather than faking one, so "no window" also satisfies this test).
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_OBSERVE_RECALL="1")
    served, window = _served_then(mod, "memory_report")
    assert window is None or window.get("ids") == served, \
        f"the client was served {served}; after memory_report the next write records {window.get('ids')}"


@pytest.mark.xfail(reason="selection_integrity: 'read-only'; with INSPEXIMUS_OBSERVE_RECALL=1 its internal "
                          "recall replaces the window, so the next write records ids the client never saw", **XFAIL)
def test_selection_integrity_leaves_the_recall_window_alone(monkeypatch, tmp_path):
    """selection_integrity: "Make SELECTION-LEVEL manipulation auditable (read-only, no LLM)." Server config:
    "INSPEXIMUS_OBSERVE_RECALL record which memories were served immediately before each write".

    core.selection_integrity calls `self.recall(query, k=k, reinforce=False)` with the default observe=True.
    With no trust root it returns no ids at all, yet the next write's window names its internal top-k.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_OBSERVE_RECALL="1")
    served, window = _served_then(mod, "selection_integrity", query="coffee")
    assert window is None or window.get("ids") == served, \
        f"the client was served {served}; after selection_integrity the next write records {window.get('ids')}"


# ── irreversible_budget_report ──────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="irreversible_budget_report: 'how much durable pull each source has spent'; the "
                          "server caches the .irrev.json sidecar on its first call and never re-reads it", **XFAIL)
def test_irreversible_budget_report_shows_a_spend_made_after_its_first_call(monkeypatch, tmp_path):
    """irreversible_budget_report: "Audit view of the per-source lifetime IRREVERSIBLE-influence budget: how
    much durable pull each source has spent against its cap -- the 'no single source can quietly entrench
    itself' ledger. Read-only."

    No MCP tool spends the budget, so every spend this report can show was made by another handle (the
    application gating its irreversible actions). core._budget_state loads `<store>.irrev.json` once into
    `self._irrev` and the per-call `refresh()` never drops it, so after the first call the server keeps
    answering `{}` -- nothing spent -- for the life of the process. A freshly started server shows the spend.
    """
    mod = load_server(monkeypatch, tmp_path)
    rid = call(mod, "remember", text="wire the refund to account 42", source="crm/alice").data["id"]
    if call(mod, "irreversible_budget_report").data != {}:
        pytest.fail("precondition: nothing has been spent yet")
    peer = Inspeximus(path=str(tmp_path / "store.json"))
    spent = peer.spend_irreversible([rid], amount=0.7, budget=1.0)
    peer.flush()
    if not spent.get("allowed") or not (tmp_path / "store.json.irrev.json").exists():
        pytest.fail(f"precondition: the peer's spend must land in the sidecar, got {spent}")
    rep = call(mod, "irreversible_budget_report").data
    assert any(abs(row.get("spent", 0) - 0.7) < 1e-9 for row in (rep or {}).values()), \
        f"a source has spent 0.7 of its 1.0 lifetime budget (sidecar on disk); the report says {rep}"
