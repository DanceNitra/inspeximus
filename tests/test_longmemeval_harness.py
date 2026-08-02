"""The LongMemEval harness has to be checkable without the 278 MB dataset and without a free GPU.

Everything here runs on a synthetic 24-instance fixture and a lexical store, so CI exercises the
parts that decide what the published number MEANS: which questions are in the subset, whether a
missing dataset fails loudly or quietly substitutes something else, whether every arm gets the same
context budget, and whether the band check can actually fail.

The last one is the important one. `band_check` is the harness's own falsification test, and a
falsification test that only ever passes has measured nothing — so it is exercised in both
directions, including the case where a control arm beats the floor.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_PY = os.path.join(ROOT, "benchmarks", "longmemeval", "run.py")


def _load():
    """Import by path, not by name: `run` is a generic module name and this must not bind to
    whatever else is importable in the environment."""
    spec = importlib.util.spec_from_file_location("longmemeval_run", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["longmemeval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load()


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────
TYPES = ["multi-session", "temporal-reasoning", "knowledge-update", "single-session-user",
         "single-session-assistant", "single-session-preference"]


def _instance(qid, qtype, n_sessions=3, turns_per=4):
    sessions, dates, sids = [], [], []
    for s in range(n_sessions):
        sid = f"{qid}-s{s}"
        sids.append(sid)
        dates.append(f"2023/05/{10 + s:02d} (Wed) 10:00")
        session = []
        for t in range(turns_per):
            msg = {"role": "user" if t % 2 == 0 else "assistant",
                   "content": f"session {s} turn {t} about topic {qid} widget {s}{t}"}
            if s == 0 and t == 1:
                msg["has_answer"] = True
            session.append(msg)
        sessions.append(session)
    return {"question_id": qid, "question_type": qtype, "question": f"what about {qid}?",
            "question_date": "2023/05/30 (Tue) 23:40", "answer": f"answer-{qid}",
            "haystack_sessions": sessions, "haystack_dates": dates,
            "haystack_session_ids": sids, "answer_session_ids": [sids[0]]}


@pytest.fixture(scope="module")
def dataset():
    """Ids are HEX-SHAPED, like the real dataset's, so that `gpt4_…` sorts AFTER them exactly as it
    does upstream (letters outrank digits). A fixture with word-shaped ids put `gpt4_` first and the
    ordering test passed while proving nothing."""
    data = []
    for i, t in enumerate(TYPES):
        for j in range(4):
            data.append(_instance(f"{i}{j}bc{i}de{j}", t))
    data.append(_instance("0fa91c33_abs", "multi-session"))
    data.append(_instance("gpt4_9de41ab7", "temporal-reasoning"))
    return data


# ── subset selection ────────────────────────────────────────────────────────────────────────────
def test_subset_is_deterministic(dataset):
    a = [x["question_id"] for x in R.select_subset(dataset, 12)]
    b = [x["question_id"] for x in R.select_subset(dataset, 12)]
    assert a == b and len(a) == 12


def test_subset_excludes_abstention(dataset):
    ids = {x["question_id"] for x in R.select_subset(dataset, len(dataset))}
    assert not any(i.endswith("_abs") for i in ids), \
        "abstention instances need an abstention rubric and are declared out of scope"


def test_subset_is_stratified(dataset):
    sub = R.select_subset(dataset, 12)
    seen = {x["question_type"] for x in sub}
    assert seen == set(TYPES), f"a stratified sample must reach every type, got {sorted(seen)}"


def test_subset_ordering_is_not_lexicographic(dataset):
    """THE discriminator for the bias this rule exists to avoid. In the real dataset 89 of the 133
    temporal-reasoning questions carry a `gpt4_` prefix, so ordering by raw id would push all of
    them behind the hex-named ones and a small temporal stratum would contain none. Here the fixture
    has one `gpt4_`-prefixed temporal question that a lexicographic sort would place last."""
    temporal = sorted([x for x in dataset if x["question_type"] == "temporal-reasoning"],
                      key=lambda x: x["question_id"])
    assert temporal[-1]["question_id"].startswith("gpt4_"), "fixture no longer poses the question"
    hashed = sorted(temporal, key=lambda x: R._sha(x["question_id"]))
    assert [x["question_id"] for x in hashed] != [x["question_id"] for x in temporal], \
        "the hash ordering must not coincide with the lexicographic one, or the test proves nothing"


def test_subset_never_exceeds_the_pool(dataset):
    assert len(R.select_subset(dataset, 10_000)) == len([x for x in dataset
                                                         if not x["question_id"].endswith("_abs")])


# ── the dataset gate: fail loudly, never substitute ──────────────────────────────────────────────
def test_a_missing_dataset_fails_loudly_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGMEMEVAL_DATA", str(tmp_path / "nope.json"))
    monkeypatch.setenv("LONGMEMEVAL_DATA_DIR", str(tmp_path / "nodir"))
    monkeypatch.setattr(R, "HERE", tmp_path / "nohere")
    monkeypatch.setattr(R.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    with pytest.raises(R.DatasetMissing) as e:
        R.resolve_dataset()
    assert e.value.code == 2
    assert "--download" in e.value.msg and "will NOT score on a substitute" in e.value.msg
    assert R.DATASET_URL in e.value.msg


def test_a_substitute_dataset_is_rejected(tmp_path):
    """A harness that scores whatever JSON it was handed is the failure this file exists against.
    LoCoMo is the substitute actually sitting on the machine this was built on."""
    locomo_shaped = tmp_path / "locomo10.json"
    locomo_shaped.write_text(json.dumps([{"conversation": {"session_1": []}, "qa": []}]),
                             encoding="utf-8")
    with pytest.raises(R.DatasetMissing) as e:
        R.load_dataset(locomo_shaped)
    assert "does not look like LongMemEval" in str(e.value.msg)


def test_a_real_shaped_dataset_is_accepted(tmp_path, dataset):
    p = tmp_path / "longmemeval_s.json"
    p.write_text(json.dumps(dataset), encoding="utf-8")
    assert len(R.load_dataset(p)) == len(dataset)


# ── context construction: one budget, every arm ─────────────────────────────────────────────────
def _store(turns):
    s = R.Inspeximus(path=None)                      # no embedder -> lexical; no network needed
    for t in turns:
        s.remember(t["text"], mtype="episodic", meta={"session_id": t["session_id"]})
    return s


def test_every_arm_respects_the_same_budget(dataset):
    inst = dataset[0]
    turns = R.instance_turns(inst)
    st, alt = _store(turns), _store(R.instance_turns(dataset[1]))
    for arm in ("oracle", "inspeximus", "recency", "shuffled"):
        ctx, _info = R.context_for(arm, inst, turns, st, alt)
        assert ctx is not None and len(ctx) <= R.CONTEXT_CHAR_BUDGET, arm


def test_the_budget_actually_bites():
    """A budget nothing ever reaches is not a budget. Feed more than fits and require truncation."""
    big = ["x" * 5000 for _ in range(20)]
    ctx, truncated = R._fill(big)
    assert truncated and len(ctx) <= R.CONTEXT_CHAR_BUDGET


def test_empty_arm_is_empty(dataset):
    ctx, _ = R.context_for("empty", dataset[0], R.instance_turns(dataset[0]), None, None)
    assert ctx == ""


def test_oracle_arm_reads_only_the_evidence_sessions(dataset):
    inst = dataset[0]
    turns = R.instance_turns(inst)
    ctx, info = R.context_for("oracle", inst, turns, None, None)
    gold = set(inst["answer_session_ids"])
    other = [t["text"] for t in turns if t["session_id"] not in gold]
    assert info["evidence_turns"] > 0
    assert all(t not in ctx for t in other), "the ceiling arm must not see non-evidence sessions"


def test_shuffled_control_reads_a_different_store(dataset):
    """The control is only a control if it really points somewhere else. Both stores are built and
    the two contexts must not coincide."""
    inst = dataset[0]
    turns = R.instance_turns(inst)
    st, alt = _store(turns), _store(R.instance_turns(dataset[5]))
    mine, _ = R.context_for("inspeximus", inst, turns, st, alt)
    theirs, _ = R.context_for("shuffled", inst, turns, st, alt)
    assert mine and theirs and mine != theirs
    assert inst["question_id"] not in theirs


def test_full_context_declares_itself_not_computable(dataset, monkeypatch):
    monkeypatch.setattr(R, "CONTEXT_CHAR_BUDGET", 50)
    ctx, info = R.context_for("full_context", dataset[0], R.instance_turns(dataset[0]), None, None)
    assert ctx is None and "Not computable" in info["not_computable"]
    assert info["haystack_chars"] > 50


def test_the_write_path_takes_no_llm(dataset):
    """`build_store` must reach nothing but the embedder. Point `chat` at a landmine and ingest."""
    def _explode(*_a, **_k):
        raise AssertionError("an LLM was called on the write path")

    turns = R.instance_turns(dataset[0])[:5]
    orig_chat, orig_embed = R.chat, R.embed_batch
    R.chat = _explode
    R.embed_batch = lambda *a, **k: None              # no network; records simply carry no vector
    try:
        store = R.build_store(turns)
    finally:
        R.chat, R.embed_batch = orig_chat, orig_embed
    assert len(store.recall("widget", k=5, mode="lexical", reinforce=False)) > 0


# ── the band check must be able to fail ─────────────────────────────────────────────────────────
def test_band_check_passes_on_a_sane_ordering():
    b = R.band_check({"recency": 0.20, "inspeximus": 0.45, "oracle": 0.70,
                      "shuffled": 0.10, "empty": 0.05})
    assert b["passed"] and b["checks"]["above_floor"] and b["checks"]["below_ceiling"]


@pytest.mark.parametrize("scores,needle", [
    ({"recency": 0.50, "inspeximus": 0.45, "oracle": 0.70}, "not strictly above"),
    ({"recency": 0.20, "inspeximus": 0.75, "oracle": 0.70}, "not strictly below"),
    ({"recency": 0.20, "inspeximus": 0.45, "oracle": 0.70, "empty": 0.60}, "scores above the floor"),
    ({"recency": 0.20, "inspeximus": 0.45, "oracle": 0.70, "shuffled": 0.55}, "scores above the floor"),
    ({"recency": 0.20, "inspeximus": 0.20, "oracle": 0.70}, "not strictly above"),
    ({"recency": 0.20, "inspeximus": None, "oracle": 0.70}, "missing a score"),
])
def test_band_check_fails_when_it_should(scores, needle):
    b = R.band_check(scores)
    assert not b["passed"]
    assert any(needle in r for r in b["reasons"]), b["reasons"]


# ── judge verdict parsing ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ('{"correct": 1}', 1),
    ('{"correct": 0}', 0),
    ('Sure! {"correct": 1} — the answer matches.', 1),
    ('{"correct": true}', 1),
    ('correct: 0', 0),
    ("", None),
    ("the answer seems fine to me", None),
    ('{"score": 1}', None),
])
def test_verdict_parsing(raw, want):
    assert R.parse_verdict(raw) == want


def test_an_unparsed_verdict_is_not_silently_a_zero():
    """It must be None, not 0. Scoring an unreadable judgement as wrong would quietly convert judge
    flakiness into a lower score for whichever arm the judge found hardest to read."""
    assert R.parse_verdict("garbage") is None


# ── the GPU gate ────────────────────────────────────────────────────────────────────────────────
def test_gpu_gate_refuses_when_the_card_is_busy(monkeypatch):
    monkeypatch.setattr(R, "gpu_state", lambda: {"nvidia_smi": True, "free_mb": 4000,
                                                 "total_mb": 24576, "util_pct": 30,
                                                 "foreign_processes": ["llama-server.exe"],
                                                 "error": None})
    with pytest.raises(SystemExit) as e:
        R.gpu_preflight(allow_contended=False)
    assert e.value.code == 3


def test_gpu_gate_refuses_when_it_cannot_read_the_card(monkeypatch):
    """Unknown must not read as free. A check that cannot see its target reporting SAFE is the
    failure mode this repository keeps rediscovering."""
    monkeypatch.setattr(R, "gpu_state", lambda: {"nvidia_smi": False, "free_mb": None,
                                                 "total_mb": None, "util_pct": None,
                                                 "foreign_processes": [], "error": "not found"})
    with pytest.raises(SystemExit) as e:
        R.gpu_preflight(allow_contended=False)
    assert e.value.code == 3


def test_gpu_gate_passes_on_a_quiet_card(monkeypatch):
    monkeypatch.setattr(R, "gpu_state", lambda: {"nvidia_smi": True, "free_mb": 23000,
                                                 "total_mb": 24576, "util_pct": 0,
                                                 "foreign_processes": [], "error": None})
    st = R.gpu_preflight(allow_contended=False)
    assert st["passed"] and not st["override"] and not st["reasons"]


def test_an_override_is_recorded_not_hidden(monkeypatch):
    monkeypatch.setattr(R, "gpu_state", lambda: {"nvidia_smi": True, "free_mb": 4000,
                                                 "total_mb": 24576, "util_pct": 30,
                                                 "foreign_processes": ["llama-server.exe"],
                                                 "error": None})
    st = R.gpu_preflight(allow_contended=True)
    assert st["override"] is True and st["passed"] is False and st["reasons"]


# ── the operating point has to be recorded, not assumed ─────────────────────────────────────────
def test_prompt_hashes_are_recorded_and_distinct():
    h = R.PROMPT_HASHES
    assert set(h) == {"answer_prompt_sha256", "judge_prompt_sha256",
                      "judge_prompt_preference_sha256"}
    assert len(set(h.values())) == 3 and all(len(v) == 64 for v in h.values())


def test_a_prompt_edit_changes_its_hash():
    """The hash is in the result JSON so a number can be traced to the exact wording that produced
    it. If it did not move with the prompt it would be decoration."""
    assert R._sha(R.JUDGE_PROMPT) != R._sha(R.JUDGE_PROMPT + " ")


def test_preference_questions_get_the_rubric_prompt():
    assert "PREFERENCE RUBRIC" in R.JUDGE_PROMPT_PREFERENCE
    assert "PREFERENCE RUBRIC" not in R.JUDGE_PROMPT


def test_committed_results_carry_their_full_operating_point():
    """A committed number without its configuration is not reproducible, and this repository has
    shipped one of those before."""
    d = os.path.join(ROOT, "benchmarks", "longmemeval", "results")
    files = [f for f in sorted(os.listdir(d)) if f.startswith("longmemeval_s_")] \
        if os.path.isdir(d) else []
    if not files:
        pytest.skip("no scored result committed yet")
    for f in files:
        r = json.loads(open(os.path.join(d, f), encoding="utf-8").read())
        op = r["operating_point"]
        for field in ("dataset_sha256", "subset_n", "question_ids", "k", "recall_mode",
                      "context_char_budget", "embedder", "answerer", "judge",
                      "judge_prompt_sha256", "llm_on_write_path"):
            assert field in op, f"{f} is missing operating point field {field!r}"
        assert op["llm_on_write_path"] is False
        assert "band_check" in r and "contended" in r
