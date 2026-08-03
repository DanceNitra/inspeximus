"""The LOCOMO benchmark's own guard rails.

Three kinds of test live here, and the split is deliberate:

  1. OFFLINE, always runs — the harness is exercised end to end against a fabricated conversation with
     a deterministic stub embedder and a stub LLM. No dataset, no GPU, no network. This is what proves
     the wiring (ingest -> recall -> answer -> judge -> controls -> band) works at all.
  2. CONTROLS-ON-THE-CONTROLS, always runs — every gate is handed an input it cannot examine its way
     out of: a floor that scores well, a ceiling that scores badly, a band that inverts, a comparison
     whose field vanished. A gate that has never been shown failing has measured nothing.
  3. REPRODUCTION, skipped with a reason — re-runs the committed result when the dataset and the local
     models are actually present, and asserts the stated tolerance.

`tests/` must not require the benchmark's dataset, so (3) skips loudly rather than passing quietly.
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BENCH = os.path.join(REPO, "benchmarks", "locomo")
for _p in (BENCH, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("inspeximus")
import harness as H  # noqa: E402


# --------------------------------------------------------------------------- fixtures

def _cfg():
    c = H.load_config()
    c["subsets"]["tiny"] = {"conversations": [0], "qa_max_questions": 4}
    # k=25 over the 46-turn fixture would return most of the store for every query, and a "shuffled"
    # context that contains everything is not a shuffled context -- the floor control read 0.40 and
    # failed, on a harness that was working. The fixture has to be big enough, and k small enough,
    # that top-k is a real selection. This is the fixture's operating point, not the benchmark's.
    c["retrieval"] = dict(c["retrieval"], k=4)
    return c


def _stub_embed(text: str):
    """Deterministic bag-of-characters vector. No model, no network, stable across runs."""
    v = [0.0] * 32
    for i, ch in enumerate(text.lower()):
        v[(ord(ch) + i % 3) % 32] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


#: Distractors, so that top-k is a genuine selection rather than "the whole store". Deliberately
#: lexically disjoint from every gold turn: if a deranged context could contain the answer by luck,
#: the floor control is measuring luck.
_FILLER = [
    "the queue at the post office moved slowly this morning",
    "I repainted the hallway a shade lighter than before",
    "the neighbours are having their roof retiled next month",
    "I finally cancelled that magazine subscription",
    "there was a power cut for about twenty minutes",
    "I have been drinking far too much coffee lately",
    "the bus timetable changed again without warning",
    "I found an old photograph behind the wardrobe",
    "the supermarket has stopped stocking my usual bread",
    "I keep forgetting to water the plants on the balcony",
    "the washing machine makes an odd noise on spin",
    "I watched a documentary about deep sea vents",
    "my phone battery lasts about half what it used to",
    "the council resurfaced the road at the junction",
    "I signed up for a library card at last",
    "there is a new bakery two streets over",
    "the smoke alarm needed a fresh battery",
    "I sorted out four boxes of paperwork",
    "the weather turned sharply colder overnight",
    "I have been meaning to fix the squeaky door",
]

SAMPLE = {
    "sample_id": "fixture",
    "conversation": {
        "speaker_a": "Ada", "speaker_b": "Ben",
        "session_1_date_time": "1 May 2024",
        "session_1": [
            {"dia_id": "D1:1", "speaker": "Ada", "text": "I adopted a tabby cat named Pixel."},
            {"dia_id": "D1:2", "speaker": "Ben", "text": "I started welding classes on Tuesdays."},
            {"dia_id": "D1:3", "speaker": "Ada", "text": "My favourite hiking trail is Cinder Ridge."},
            {"dia_id": "D1:4", "speaker": "Ben", "text": "I sold my old blue bicycle last winter."},
        ] + [{"dia_id": f"D1:{100 + i}", "speaker": "Ada" if i % 2 else "Ben", "text": t}
             for i, t in enumerate(_FILLER)],
        "session_2_date_time": "9 June 2024",
        "session_2": [
            {"dia_id": "D2:1", "speaker": "Ada", "text": "Pixel knocked a mug off the shelf again."},
            {"dia_id": "D2:2", "speaker": "Ben", "text": "The welding teacher is called Mr Oyelaran."},
        ] + [{"dia_id": f"D2:{100 + i}", "speaker": "Ben" if i % 2 else "Ada", "text": t}
             for i, t in enumerate(reversed(_FILLER))],
    },
    "qa": [
        {"question": "What is the name of Ada's cat?", "answer": "Pixel",
         "evidence": ["D1:1"], "category": 4},
        {"question": "What did Ben start doing on Tuesdays?", "answer": "welding classes",
         "evidence": ["D1:2"], "category": 4},
        {"question": "Which trail does Ada like hiking?", "answer": "Cinder Ridge",
         "evidence": ["D1:3"], "category": 4},
        {"question": "What did Ben sell last winter?", "answer": "his old blue bicycle",
         "evidence": ["D1:4"], "category": 4},
        {"question": "Who teaches Ben's welding class?", "answer": "Mr Oyelaran",
         "evidence": ["D2:2"], "category": 4},
        {"question": "unanswerable by design", "answer": "n/a", "evidence": ["D9:9"], "category": 5},
    ],
}


class StubLLM:
    """An answerer that can only read the context, and a judge that compares strings.

    The answerer returns the first context line containing a gold token, else a refusal — so an arm
    whose context does not contain the answer CANNOT score, which is exactly the property the floor
    controls assert. Nothing here reaches the network.
    """

    def __init__(self, cfg, golds):
        self.answerer = cfg["qa"]["answerer_model"]
        self.judge = cfg["qa"]["judge_model"]
        self.golds = golds
        self.calls = 0
        self.errors = 0
        self.latencies = []
        self.suspected_cache_hits = 0
        self.by_model = {}
        self.cache_hit_threshold_s = 0.05

    def __call__(self, model, prompt, max_tokens):
        self.calls += 1
        if model == self.judge:
            gold = prompt.split("Gold answer:", 1)[1].split("\n", 1)[0].strip()
            pred = prompt.split("Model answer:", 1)[1].split("\n\n", 1)[0].strip()
            key = gold.lower().split()[-1]
            return "YES" if key and key in pred.lower() else "NO"
        ctx = prompt.split("Memory:\n", 1)[1].rsplit("\n\nQuestion:", 1)[0]
        question = prompt.rsplit("Question:", 1)[1].rsplit("Answer:", 1)[0].strip()
        gold = self.golds.get(question, "")
        key = gold.lower().split()[-1] if gold else ""
        for line in ctx.splitlines():
            if key and key in line.lower():
                return line
        return "I don't know."

    def stats(self):
        return {"llm_calls": self.calls, "llm_errors": 0, "llm_p50_s": None, "llm_p95_s": None,
                "llm_max_s": None, "suspected_cache_hits": 0, "cache_hit_threshold_s": 0.05,
                "by_model": {}}


def _offline_run(cfg):
    """Full pipeline on the fixture, no dataset / GPU / network."""
    cache = H.EmbedCache("stub", "http://127.0.0.1:1/none", 8,
                         path=os.path.join(BENCH, ".cache", "test_stub.json"))
    cache.store = {}
    cache.warm = lambda texts, progress=None: None
    cache.get = _stub_embed
    cache.install = lambda texts: None
    H.LQ.nomic_embed = lambda ts: [_stub_embed(t) for t in ts]
    H.LQ._embed_one = _stub_embed
    H.LQ._EMB_CACHE.clear()

    qs = H.answerable_questions(SAMPLE, cfg)
    store, turns = H.build_store(SAMPLE, cache, qs)
    llm = StubLLM(cfg, {q["question"]: str(q["answer"]) for q in qs})
    built = H.build_contexts(store, turns, qs, cfg)
    arms = {a: H.score_arm(llm, cfg, qs, built["contexts"][a], a) for a in H.QA_ARMS}
    r = cfg["retrieval"]
    retrieval = H.retrieval_recall(store, turns, qs, r["k"], r["mode"], r["reinforce"])
    return {"qs": qs, "store": store, "turns": turns, "built": built, "arms": arms,
            "retrieval": retrieval, "llm": llm}


# --------------------------------------------------------------------------- 1. offline wiring

def test_question_filter_drops_category_5_and_keeps_the_rest():
    qs = H.answerable_questions(SAMPLE, _cfg())
    assert [q["category"] for q in qs] == [4, 4, 4, 4, 4]
    assert all(q["question"] != "unanswerable by design" for q in qs)


def test_offline_pipeline_runs_and_controls_pass():
    cfg = _cfg()
    out = _offline_run(cfg)
    arms = out["arms"]
    assert set(arms) == set(H.QA_ARMS)
    controls = H.evaluate_controls(cfg, arms)
    # The two DETERMINISTIC controls, asserted exactly: with no context the stub answerer cannot
    # answer, and with the answer written into the store it must.
    assert controls["floor_empty"]["accuracy"] == 0.0, arms["floor_empty"]
    assert controls["ceiling_verbatim"]["accuracy"] >= cfg["controls"]["ceiling_verbatim_min"]

    # floor_shuffled is asserted STRUCTURALLY here, not against the production bound. Measured: this
    # 5-question fixture scores 0.2 on one machine and 0.4 on CI -- a single question answered by luck
    # from a deranged context is worth 0.20, so a 0.35 bound calibrated for the real benchmark's 150
    # questions is decided by coin-flips at n=5. Asserting it here made CI red while the harness was
    # working. What must hold at any n is the ordering: someone else's context must not rival the
    # correct one. The production bound is still enforced where it has the sample size to mean
    # something -- against the committed result, in test_committed_small_result_passed_its_own_controls
    # -- and evaluate_controls' own pass/fail logic is pinned by the synthetic tests below.
    assert arms["floor_shuffled"]["accuracy"] < arms["ceiling_verbatim"]["accuracy"], arms
    assert arms["floor_shuffled"]["accuracy"] < arms["inspeximus"]["accuracy"], arms
    # the store really was built and really retrieves
    assert out["retrieval"]["n"] == 5
    assert out["retrieval"]["recall_any"] > 0.0


def test_ceiling_records_are_erased_by_id_and_the_count_is_checked():
    """The ceiling control writes one keyed record per question and must erase every one of them.

    This test found a real defect: the cleanup passed the KEY to `forget()`, which takes record IDS.
    It erased nothing, raised nothing, and returned a normal-looking `{"forgotten": 0}` that a bare
    `try/except` then hid — a cleanup that never saw its target reporting success. The fix erases by
    id and checks the count; this asserts the count, not the absence of an exception.
    """
    cfg = _cfg()
    out = _offline_run(cfg)
    built = out["built"]
    n = len(out["qs"])
    assert built["ceiling_records_written"] == n
    assert built["ceiling_records_forgotten"] == n, built["ceiling_forget_error"]
    assert built["ceiling_cleanup_complete"] is True
    assert built["ceiling_forget_error"] is None

    # And prove it against the store, matching the exact control texts rather than a prefix. An
    # earlier version matched on "Ada: [" -- which is also how every ordinary turn renders, because
    # conv_turns prepends the session date. The discriminator has to discriminate.
    control = set(built["ceiling_texts"])
    live = {r.get("text") for r in out["store"].recall("Pixel welding Cinder bicycle Oyelaran",
                                                       k=60, mode="hybrid", reinforce=False)}
    assert not (control & live), f"ceiling control records survived: {sorted(control & live)}"


def test_ceiling_records_do_not_accumulate_across_questions():
    """Keyed writes must supersede: if they accumulated, question 5 would see the answers to 1-4 and
    the control would be measuring leakage rather than a working chain."""
    cfg = _cfg()
    out = _offline_run(cfg)
    contexts = out["built"]["contexts"]["ceiling_verbatim"]
    texts = out["built"]["ceiling_texts"]
    for i, ctx in enumerate(contexts):
        others = [t for j, t in enumerate(texts) if j != i]
        leaked = [t for t in others if t in ctx]
        assert not leaked, f"question {i} saw another question's control record: {leaked}"


def test_no_llm_on_the_write_path():
    """Ingest must make zero model calls. The write path is inspeximus's ordinary remember()."""
    cfg = _cfg()
    cache = H.EmbedCache("stub", "http://127.0.0.1:1/none", 8,
                         path=os.path.join(BENCH, ".cache", "test_stub.json"))
    cache.store = {}
    cache.warm = lambda texts, progress=None: None
    cache.get = _stub_embed
    cache.install = lambda texts: None
    H.LQ._EMB_CACHE.clear()
    llm = StubLLM(cfg, {})
    H.build_store(SAMPLE, cache, H.answerable_questions(SAMPLE, cfg))
    assert llm.calls == 0


def test_derangement_has_no_fixed_point_and_is_seed_stable():
    a = H.derangement(20, 20260801)
    assert sorted(a) == list(range(20))
    assert all(a[i] != i for i in range(20))
    assert a == H.derangement(20, 20260801)
    assert H.derangement(1, 7) == [0]        # degenerate, must not hang


def test_shuffled_floor_really_hands_over_someone_elses_context():
    cfg = _cfg()
    out = _offline_run(cfg)
    ctx = out["built"]["contexts"]
    assert all(ctx["floor_shuffled"][i] != ctx["inspeximus"][i] for i in range(len(out["qs"])))


# --------------------------------------------------------------------------- 2. controls on the controls

def test_a_floor_that_scores_well_fails_the_control():
    cfg = _cfg()
    arms = {"floor_empty": {"accuracy": 0.90}, "floor_shuffled": {"accuracy": 0.05},
            "ceiling_verbatim": {"accuracy": 1.0}}
    c = H.evaluate_controls(cfg, arms)
    assert c["floor_empty"]["passed"] is False
    assert c["all_passed"] is False
    assert "not measuring memory" in c["floor_empty"]["why"]


def test_a_ceiling_that_scores_badly_fails_the_control():
    cfg = _cfg()
    arms = {"floor_empty": {"accuracy": 0.0}, "floor_shuffled": {"accuracy": 0.05},
            "ceiling_verbatim": {"accuracy": 0.20}}
    c = H.evaluate_controls(cfg, arms)
    assert c["ceiling_verbatim"]["passed"] is False
    assert c["all_passed"] is False


def test_a_control_that_never_ran_fails_rather_than_passing():
    cfg = _cfg()
    c = H.evaluate_controls(cfg, {"floor_empty": {"accuracy": 0.0}})
    assert c["floor_shuffled"]["passed"] is False
    assert c["ceiling_verbatim"]["passed"] is False
    assert c["all_passed"] is False


def test_band_requires_inspeximus_strictly_between_floor_and_ceiling():
    cfg = _cfg()
    ok = H.evaluate_band(cfg, {"naive_recency": {"accuracy": 0.20},
                               "inspeximus": {"accuracy": 0.55},
                               "fullcontext": {"accuracy": 0.70}})
    assert ok["passed"] is True
    under = H.evaluate_band(cfg, {"naive_recency": {"accuracy": 0.60},
                                  "inspeximus": {"accuracy": 0.55},
                                  "fullcontext": {"accuracy": 0.70}})
    assert under["passed"] is False and under["above_floor"] is False
    over = H.evaluate_band(cfg, {"naive_recency": {"accuracy": 0.20},
                                 "inspeximus": {"accuracy": 0.75},
                                 "fullcontext": {"accuracy": 0.70}})
    assert over["passed"] is False and over["below_ceiling"] is False
    tie = H.evaluate_band(cfg, {"naive_recency": {"accuracy": 0.55},
                                "inspeximus": {"accuracy": 0.55},
                                "fullcontext": {"accuracy": 0.70}})
    assert tie["passed"] is False, "equal to the floor is not strictly above it"
    missing = H.evaluate_band(cfg, {"inspeximus": {"accuracy": 0.55}})
    assert missing["passed"] is False and "did not run" in missing["why"]


def test_tolerance_comparison_catches_drift():
    """The check that the reproduction check can fail. Without this, a green suite cannot tell
    'the harness reproduces' from 'the harness stopped producing the number'."""
    tol = {"qa_accuracy": 0.10, "retrieval_recall": 0.02, "control_accuracy": 0.15}
    base = {"retrieval": {"pinned": {"recall_any": 0.80, "recall_all": 0.67}},
            "qa": {"arms": {"inspeximus": {"accuracy": 0.60}, "floor_empty": {"accuracy": 0.00}}}}
    same = H.compare_to_baseline(base, copy.deepcopy(base), tol)
    assert same["within_tolerance"] is True and same["n_checked"] == 4

    drifted = copy.deepcopy(base)
    drifted["retrieval"]["pinned"]["recall_any"] = 0.90            # +0.10, tolerance 0.02
    out = H.compare_to_baseline(base, drifted, tol)
    assert out["within_tolerance"] is False and out["n_outside"] == 1

    nudged = copy.deepcopy(base)
    nudged["retrieval"]["pinned"]["recall_any"] = 0.815            # +0.015, inside 0.02
    assert H.compare_to_baseline(base, nudged, tol)["within_tolerance"] is True


def test_tolerance_comparison_fails_on_a_field_that_vanished():
    tol = {"qa_accuracy": 0.10, "retrieval_recall": 0.02, "control_accuracy": 0.15}
    base = {"retrieval": {"pinned": {"recall_any": 0.80, "recall_all": 0.67}},
            "qa": {"arms": {"inspeximus": {"accuracy": 0.60}}}}
    gone = {"retrieval": {"pinned": {"recall_any": 0.80, "recall_all": 0.67}}, "qa": {"arms": {}}}
    out = H.compare_to_baseline(base, gone, tol)
    assert out["within_tolerance"] is False
    assert any(d["why"] == "field missing on one side" for d in out["fields"] if "why" in d)


def test_empty_comparison_is_not_a_pass():
    tol = {"qa_accuracy": 0.10, "retrieval_recall": 0.02, "control_accuracy": 0.15}
    assert H.compare_to_baseline({}, {}, tol)["within_tolerance"] is False


def test_published_comparison_reports_a_mismatch_as_a_mismatch():
    cfg = _cfg()
    ref = cfg["published_reference"]
    near = H.compare_to_published(cfg, {"pinned": {"k": 25, "n": ref["n"], "reinforce": False,
                                                   "recall_any": ref["recall_any"] + 0.01,
                                                   "recall_all": ref["recall_all"] - 0.01}})
    assert near["arms"]["pinned"]["matches_published"] is True
    far = H.compare_to_published(cfg, {"pinned": {"k": 25, "n": ref["n"], "reinforce": False,
                                                  "recall_any": ref["recall_any"] + 0.20,
                                                  "recall_all": ref["recall_all"]}})
    assert far["arms"]["pinned"]["matches_published"] is False
    assert far["any_arm_matches_published"] is False


def test_verdict_parser_never_guesses():
    assert H.parse_verdict("YES") is True
    assert H.parse_verdict("no") is False
    assert H.parse_verdict("Yes, the meaning matches.") is True
    assert H.parse_verdict("") is None
    assert H.parse_verdict("__ERR__timeout") is None
    assert H.parse_verdict("maybe?") is None, "an unparseable verdict must not be scored as correct"
    assert H.parse_verdict("YESTERDAY") is None, "a substring is not a verdict"


def test_missing_dataset_skips_with_a_reason_and_never_substitutes(tmp_path, monkeypatch):
    cfg = _cfg()
    cfg["dataset"] = dict(cfg["dataset"], file="definitely-absent-locomo.json")
    monkeypatch.delenv(H.DATASET_ENV, raising=False)
    monkeypatch.delenv(H.DATASET_ENV_LEGACY, raising=False)
    with pytest.raises(H.DatasetMissing) as e:
        H.resolve_dataset(cfg)
    msg = str(e.value)
    assert "Looked in" in msg and cfg["dataset"]["source"] in msg
    assert cfg["dataset"]["sha256"] in msg


def test_dataset_env_var_is_honoured(tmp_path, monkeypatch):
    f = tmp_path / "locomo10.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setenv(H.DATASET_ENV, str(f))
    assert H.resolve_dataset(_cfg()) == str(f.resolve())


def test_dataset_sha_mismatch_is_refused(tmp_path):
    f = tmp_path / "locomo10.json"
    f.write_text("[]", encoding="utf-8")
    with pytest.raises(H.DatasetMissing) as e:
        H.check_dataset_sha(_cfg(), str(f), allow_drift=False)
    assert "sha256 mismatch" in str(e.value)
    drifted = H.check_dataset_sha(_cfg(), str(f), allow_drift=True)
    assert drifted["matches_pin"] is False and drifted["drift_allowed"] is True


def _quiet_gpu(monkeypatch, free=23000, ours=0, runners=([], [], None)):
    monkeypatch.setattr(H, "_nvidia_free_mb", lambda: (free, 24576, None))
    monkeypatch.setattr(H, "_ollama_resident", lambda cfg: (ours, [], None))
    monkeypatch.setattr(H, "_gpu_runners", lambda cfg: runners)


def test_gpu_preflight_refuses_when_vram_is_short(monkeypatch):
    cfg = _cfg()
    _quiet_gpu(monkeypatch, free=1024)
    with pytest.raises(H.GpuBusy) as e:
        H.gpu_preflight(cfg, allow_shared=False)
    assert "free VRAM" in str(e.value)
    state = H.gpu_preflight(cfg, allow_shared=True)
    assert state["contended"] is True and state["override_used"] is True


def test_gpu_preflight_credits_back_our_own_resident_models(monkeypatch):
    """Our own loaded models must not lock us out of our own second run — but they must not paper
    over a genuinely full card either."""
    cfg = _cfg()
    _quiet_gpu(monkeypatch, free=4800, ours=15375)
    ok = H.gpu_preflight(cfg, allow_shared=False)
    assert ok["contended"] is False and ok["effective_free_vram_mb"] == 20175
    _quiet_gpu(monkeypatch, free=4800, ours=0)
    with pytest.raises(H.GpuBusy):
        H.gpu_preflight(cfg, allow_shared=False)


def test_gpu_preflight_distinguishes_our_runner_from_a_foreign_one(monkeypatch):
    """The rule this replaced forbade `llama-server.exe` outright — which is exactly how Ollama runs a
    model, so it could never pass once a model loaded. Ours must pass; a foreign one must refuse."""
    cfg = _cfg()
    _quiet_gpu(monkeypatch, runners=(["llama-server.exe"], [], None))
    assert H.gpu_preflight(cfg, allow_shared=False)["contended"] is False

    _quiet_gpu(monkeypatch, runners=(["llama-server.exe"], ["vllm.exe"], None))
    with pytest.raises(H.GpuBusy) as e:
        H.gpu_preflight(cfg, allow_shared=False)
    assert "foreign model runner" in str(e.value) and "vllm.exe" in str(e.value)


def test_gpu_runner_classification_reads_the_command_line(monkeypatch):
    """The ours/foreign split is the load-bearing part; test it on real-shaped process rows."""
    cfg = _cfg()
    rows = ("ollama.exe|C:\\Users\\x\\AppData\\Local\\Programs\\Ollama\\ollama.exe serve\n"
            "llama-server.exe|C:\\Users\\x\\AppData\\Local\\Programs\\Ollama\\lib\\ollama\\"
            "llama-server.exe --model C:\\models\\a.gguf\n"
            "llama-server.exe|D:\\someone-else\\llama.cpp\\llama-server.exe --model D:\\b.gguf\n"
            "chrome.exe|C:\\Program Files\\Chrome\\chrome.exe\n")

    class _Out:
        stdout = rows

    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: _Out())
    ours, foreign, err = H._gpu_runners(cfg)
    assert err is None
    assert ours == ["llama-server.exe"]
    assert foreign == ["llama-server.exe"], "a runner outside the Ollama install is somebody else's"
    assert len(ours) == 1 and len(foreign) == 1


def test_gpu_window_flags_a_card_that_changed_mid_run():
    """The check that would have caught 2026-08-02: a scheduled task restarted two ~20 GB services in
    the middle of a 90-minute run, the pre-flight had already stamped contended=false, and the only
    symptom was per-call latency going from 2.6 s to 75 s. Sampling one endpoint cannot see that."""
    cfg = _cfg()
    clean = H.gpu_window(
        cfg,
        {"free_vram_mb": 21000, "own_models_vram_mb": 0, "foreign_runners": []},
        {"free_vram_mb": 5600, "own_models_vram_mb": 15400, "foreign_runners": []})
    assert clean["stable"] is True and clean["drift"] == [], clean

    invaded = H.gpu_window(
        cfg,
        {"free_vram_mb": 21000, "own_models_vram_mb": 0, "foreign_runners": []},
        {"free_vram_mb": 4670, "own_models_vram_mb": 0, "foreign_runners": ["llama-server.exe"]})
    assert invaded["stable"] is False
    assert any("foreign runner" in d for d in invaded["drift"]), invaded
    assert any("VRAM went to something other than our own models" in d for d in invaded["drift"])

    # A big drop with no foreign runner named is still drift: whatever took 16 GB is not ours.
    silent = H.gpu_window(
        cfg,
        {"free_vram_mb": 21000, "own_models_vram_mb": 0, "foreign_runners": []},
        {"free_vram_mb": 4670, "own_models_vram_mb": 0, "foreign_runners": []})
    assert silent["stable"] is False, silent


def test_gpu_window_records_both_endpoints():
    """A reader must be able to tell a contended run from a clean one from the artifact alone."""
    cfg = _cfg()
    w = H.gpu_window(cfg, {"free_vram_mb": 100, "own_models_vram_mb": 0, "foreign_runners": []},
                     {"free_vram_mb": 100, "own_models_vram_mb": 0, "foreign_runners": []})
    assert set(w) >= {"start", "end", "drift", "stable"}
    assert w["start"]["free_vram_mb"] == 100 and w["end"]["free_vram_mb"] == 100


def test_gpu_preflight_refuses_when_it_cannot_read_the_gpu(monkeypatch):
    """A pre-flight that cannot see its target must not report clear."""
    monkeypatch.setattr(H, "_nvidia_free_mb", lambda: (None, None, "nvidia-smi not found"))
    monkeypatch.setattr(H, "_ollama_resident", lambda cfg: (0, [], None))
    monkeypatch.setattr(H, "_gpu_runners", lambda cfg: ([], [], None))
    with pytest.raises(H.GpuBusy):
        H.gpu_preflight(_cfg(), allow_shared=False)


def test_gpu_preflight_refuses_when_it_cannot_enumerate_processes(monkeypatch):
    """Same class: if the foreign-runner check cannot run, it has not found 'no foreign runners'."""
    _quiet_gpu(monkeypatch, runners=([], [], "could not enumerate processes"))
    with pytest.raises(H.GpuBusy) as e:
        H.gpu_preflight(_cfg(), allow_shared=False)
    assert "enumerate" in str(e.value)


def test_prompt_fingerprints_change_when_a_prompt_changes(monkeypatch):
    """The judge prompt is part of the operating point. If it can be edited without the result file
    noticing, the number drifts with no visible cause."""
    before = H.prompt_fingerprints()
    assert set(before) == {"answer_prompt_sha256", "judge_prompt_sha256"}
    assert len(before["judge_prompt_sha256"]) == 64
    monkeypatch.setattr(H, "JUDGE_PROMPT", H.JUDGE_PROMPT + " Be lenient.")
    after = H.prompt_fingerprints()
    assert after["judge_prompt_sha256"] != before["judge_prompt_sha256"]
    assert after["answer_prompt_sha256"] == before["answer_prompt_sha256"]


# --------------------------------------------------------------------------- 3. the committed result

def _committed(name="small.json"):
    p = os.path.join(BENCH, "results", name)
    if not os.path.exists(p):
        pytest.fail(f"the committed result {name} is missing — a benchmark with no committed number "
                    f"cannot be reproduced by anyone")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def test_committed_results_exist_and_carry_their_operating_point():
    for name in ("small.json", "full_retrieval.json"):
        r = _committed(name)
        assert r["benchmark"] == "locomo-e2e"
        op = r["operating_point"]
        for field in ("retrieval", "qa", "seed", "controls", "tolerance", "published_reference"):
            assert field in op, f"{name} lost {field} from its operating point"
        assert r["dataset"]["matches_pin"] is True, f"{name} was measured on an unpinned dataset"
        assert r["dataset_drift"] is False


def test_committed_small_result_passed_its_own_controls():
    r = _committed("small.json")
    assert r["controls"]["all_passed"] is True, r["controls"]
    assert r["band"]["passed"] is True, r["band"]
    assert r["judge_calibration"]["gate_passed"] is True, r["judge_calibration"]
    for arm in H.QA_ARMS:
        assert arm in r["qa"]["arms"], f"committed result is missing the {arm} arm"


def test_committed_qa_numbers_were_graded_by_the_judge_this_repo_ships():
    """The committed QA numbers, the judge that graded them, and the gate that vetted that judge must
    all refer to the SAME judge prompt.

    Checked at the source rather than via a boolean the runner computed: `small.json` records the
    prompt fingerprints of the run that produced it, and `judge_calibration.json` records the prompt
    the gate was run against. If either drifts from the prompt in `harness.py`, the committed numbers
    were graded by something no longer in the repository, and the gate vetted something else again.
    """
    want = H.prompt_fingerprints()["judge_prompt_sha256"]
    r = _committed("small.json")
    assert r["operating_point"]["prompts"]["judge_prompt_sha256"] == want, (
        "the judge prompt in harness.py has changed since results/small.json was measured — re-run "
        "the benchmark and re-cut the baseline, or the committed QA numbers describe a judge that is "
        "no longer shipped")

    cal_path = os.path.join(BENCH, "results", "judge_calibration.json")
    assert os.path.exists(cal_path), "no committed judge calibration: the QA numbers are unvetted"
    with open(cal_path, encoding="utf-8") as fh:
        cal = json.load(fh)
    assert cal["prompts"]["judge_prompt_sha256"] == want, (
        "the judge gate was run against a different prompt than the one shipped")
    assert cal["judge_model"] == r["operating_point"]["qa"]["judge_model"]
    assert cal["gate_passed"] is True
    for arm in ("GOLD", "WRONG", "REFUSAL"):
        assert cal["arms"][arm]["n"] > 0, f"the {arm} arm had no cases, so it vetted nothing"
        assert cal["arms"][arm]["rate"] >= cal["min_rate"], cal["arms"][arm]


def test_committed_small_result_was_measured_cleanly():
    """Zero failed calls, zero unparseable verdicts, zero cache-hit-shaped replies, and a ceiling
    control that actually erased itself. Any of these non-zero means the arms are understated."""
    r = _committed("small.json")
    assert r["latency"]["llm_errors"] == 0
    assert r["latency"]["suspected_cache_hits"] == 0
    assert all(a["parse_fail"] == 0 for a in r["qa"]["arms"].values()), r["qa"]["arms"]
    assert r["qa"]["ceiling_cleanup_complete"] is True
    assert r["qa"]["ceiling_records_forgotten"] == r["qa"]["ceiling_records_written"]
    assert r["gpu"]["contended"] is False, "the committed QA number must be a quiesced measurement"
    assert r["gpu_window"]["stable"] is True, (
        f"the card changed underneath the committed run: {r['gpu_window']['drift']} — the QA numbers "
        f"span two regimes and must be re-measured")


def test_committed_small_result_is_within_its_own_tolerance():
    """Self-consistency: comparing the committed result to itself must be inside tolerance. If this
    ever fails, the comparison is broken, not the number."""
    r = _committed("small.json")
    cmp = H.compare_to_baseline(r, r, H.load_config()["tolerance"])
    assert cmp["within_tolerance"] is True and cmp["n_checked"] >= 8


def test_committed_full_retrieval_states_whether_it_matches_the_published_pair():
    """The decisive check, frozen into the committed artifact: whatever the answer is, it is recorded
    and the README must agree with it."""
    r = _committed("full_retrieval.json")
    pub = r["retrieval"]["published_comparison"]
    assert pub["reference"]["recall_any"] == 0.783
    assert pub["reference"]["recall_all"] == 0.648
    for arm in ("pinned", "published_config"):
        row = pub["arms"][arm]
        assert row["same_denominator_as_published"] is True, (
            f"{arm} was measured on n={row['n']} but the published pair is n={pub['reference']['n']}; "
            f"a different denominator is not a reproduction")
        assert isinstance(row["matches_published"], bool)


def test_readme_locomo_numbers_come_from_the_committed_result():
    """The README's LOCOMO pair must be the number the harness prints, to 2 decimals. This is the test
    that stops the published figure and the reproducible figure drifting apart again."""
    r = _committed("full_retrieval.json")
    got = r["retrieval"]["pinned"]
    with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    block = readme.split("## And it doesn't cost you recall", 1)
    assert len(block) == 2, "the README section this test guards has been renamed"
    section = block[1][:2600]
    for metric in ("recall_any", "recall_all"):
        assert f"{got[metric]:.2f}" in section, (
            f"README does not state the reproduced {metric} {got[metric]:.2f}; "
            f"correct the README to what benchmarks/locomo prints")
    assert "benchmarks/locomo" in section, "the README must name the harness that produces the number"


# --------------------------------------------------------------------------- reproduction (gated)

def _live_dataset():
    try:
        p = H.resolve_dataset(H.load_config())
        H.check_dataset_sha(H.load_config(), p, allow_drift=False)
        return p
    except H.DatasetMissing:
        return None


@pytest.mark.skipif(os.environ.get("LOCOMO_REPRODUCE") != "1",
                    reason="set LOCOMO_REPRODUCE=1 (needs the LOCOMO dataset + a local embedder); "
                           "this re-runs the retrieval half and asserts the committed tolerance")
def test_retrieval_reproduces_the_committed_result():
    path = _live_dataset()
    if not path:
        pytest.skip("LOCOMO dataset absent — nothing to reproduce (this is a SKIP, not a pass)")
    cfg = H.load_config()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cache = H.EmbedCache(cfg["retrieval"]["embedder"], cfg["retrieval"]["embed_endpoint"],
                         cfg["retrieval"]["embed_batch"])
    r = cfg["retrieval"]
    parts = []
    for _ci, sample in H.subset_samples(data, cfg, "small"):
        qs = H.answerable_questions(sample, cfg)
        store, turns = H.build_store(sample, cache, qs)
        parts.append(H.retrieval_recall(store, turns, qs, r["k"], r["mode"], r["reinforce"]))
    current = {"retrieval": {"pinned": H.merge_retrieval(parts)}, "qa": {"arms": {}}}
    base = _committed("small.json")
    cmp = H.compare_to_baseline({"retrieval": {"pinned": base["retrieval"]["pinned"]},
                                 "qa": {"arms": {}}}, current, cfg["tolerance"])
    assert cmp["within_tolerance"], cmp["fields"]
