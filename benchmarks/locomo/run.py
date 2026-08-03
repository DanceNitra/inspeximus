"""LOCOMO end-to-end QA + retrieval recall — the one documented command.

    python benchmarks/locomo/run.py --subset small        # compares against the committed result
    python benchmarks/locomo/run.py --subset small --update-baseline
    python benchmarks/locomo/run.py --subset full --allow-shared-gpu   # needs a quiesced GPU

By default the run RE-MEASURES and then COMPARES against `results/<subset>.json`, exiting non-zero if
any published field drifts outside the tolerance in `config.json`. Drift is therefore caught by the
command itself and by `tests/test_locomo_benchmark.py`, not discovered months later in a README.

Exit codes — distinct on purpose, so "it skipped" can never be read as "it passed":
    0  ran, controls passed, within tolerance of the committed result
    1  ran, but a CONTROL failed, the BAND failed, or a number drifted outside tolerance
    2  the judge did not clear its calibration gate -> the run is void
    3  the dataset is absent or its sha256 does not match the pin -> SKIPPED with a reason
    4  the GPU pre-flight refused to start (contended); pass --allow-shared-gpu to measure anyway
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import harness as H  # noqa: E402
import judge_calibration as JC  # noqa: E402


def _bar(label, done, total, ok=None):
    print(f"    {label:24} {done}/{total}   ", end="\r", flush=True)


def run(cfg: dict, data: list, subset: str, args) -> dict:
    t0 = time.time()
    spec = cfg["subsets"][subset]
    cache = H.EmbedCache(cfg["retrieval"]["embedder"], cfg["retrieval"]["embed_endpoint"],
                         cfg["retrieval"]["embed_batch"])
    llm = None if args.retrieval_only else H.LLM(cfg)

    liveness = {}
    if llm is not None:
        for role in ("answerer_model", "judge_model"):
            liveness[role] = llm.probe(cfg["qa"][role])
            print(f"  liveness {role:14} {cfg['qa'][role]:14} "
                  f"answer={liveness[role]['answer']!r} correct={liveness[role]['answer_correct']}",
                  flush=True)
        dead = [r for r, v in liveness.items() if not v["alive"]]
        if dead:
            raise SystemExit(f"model(s) did not answer at all: {dead}")

    pinned_parts, published_parts = [], []
    arm_parts: dict = {a: [] for a in H.QA_ARMS}
    qa_questions, qa_rows = 0, []
    # Accumulated ACROSS conversations. An earlier version kept `ctx_meta` from the last conversation
    # only, so on a multi-conversation subset the ceiling-cleanup counters described one conversation
    # and silently spoke for all of them.
    ceiling = {"written": 0, "forgotten": 0, "retrieved": 0.0, "convs": 0, "cleanup_complete": True,
               "errors": [], "fullcontext_truncated": False, "conversation_chars": 0}

    for ci, sample in H.subset_samples(data, cfg, subset):
        qs = H.answerable_questions(sample, cfg)
        qa_qs = qs[:spec["qa_max_questions"]] if spec["qa_max_questions"] else qs
        print(f"\n  conv{ci}: {len(qs)} answerable questions "
              f"({len(qa_qs)} into end-to-end QA)", flush=True)

        store, turns = H.build_store(sample, cache, qs)
        print(f"    store built: {len(turns)} turns, embeddings "
              f"{cache.stats()['embed_http_calls']} http call(s) this run", flush=True)

        r = cfg["retrieval"]
        pinned_parts.append(H.retrieval_recall(store, turns, qs, r["k"], r["mode"], r["reinforce"]))
        print(f"    retrieval  pinned(reinforce={r['reinforce']}): "
              f"any={pinned_parts[-1]['recall_any']} all={pinned_parts[-1]['recall_all']} "
              f"n={pinned_parts[-1]['n']}", flush=True)
        # The published probe ran with recall()'s DEFAULT reinforce=True, which mutates the store.
        # It is measured second, so it cannot contaminate the pinned arm.
        published_parts.append(H.retrieval_recall(store, turns, qs, r["k"], r["mode"], True))
        print(f"    retrieval  published(reinforce=True): "
              f"any={published_parts[-1]['recall_any']} all={published_parts[-1]['recall_all']}",
              flush=True)

        if llm is None or not qa_qs:
            continue

        built = H.build_contexts(store, turns, qa_qs, cfg)
        ceiling["written"] += built["ceiling_records_written"]
        ceiling["forgotten"] += built["ceiling_records_forgotten"]
        ceiling["retrieved"] += (built["ceiling_retrieved_rate"] or 0.0) * len(qa_qs)
        ceiling["convs"] += 1
        ceiling["cleanup_complete"] &= bool(built["ceiling_cleanup_complete"])
        if built["ceiling_forget_error"]:
            ceiling["errors"].append(built["ceiling_forget_error"])
        ceiling["fullcontext_truncated"] |= bool(built["fullcontext_truncated"])
        ceiling["conversation_chars"] = max(ceiling["conversation_chars"], built["conversation_chars"])
        qa_questions += len(qa_qs)
        for arm in H.QA_ARMS:
            res = H.score_arm(llm, cfg, qa_qs, built["contexts"][arm], arm, on_item=_bar)
            arm_parts[arm].append(res)
            qa_rows.extend(res.pop("rows"))
            print(f"    {arm:16} acc={res['accuracy']} ({res['correct']}/{res['n']}) "
                  f"ctx~{res['mean_context_chars']}c", flush=True)

    retrieval = {"pinned": H.merge_retrieval(pinned_parts),
                 "published_config": H.merge_retrieval(published_parts)}
    retrieval["published_comparison"] = H.compare_to_published(cfg, retrieval)

    result = {"benchmark": cfg["benchmark"], "config_version": cfg["config_version"], "subset": subset,
              "retrieval": retrieval}

    if llm is not None and qa_questions:
        arms = {a: H.merge_arm(arm_parts[a]) for a in H.QA_ARMS if arm_parts[a]}
        result["qa"] = {
            "n": qa_questions, "n_conversations": ceiling["convs"], "arms": arms,
            "ceiling_retrieved_rate": round(ceiling["retrieved"] / qa_questions, 4),
            "ceiling_records_written": ceiling["written"],
            "ceiling_records_forgotten": ceiling["forgotten"],
            "ceiling_cleanup_complete": bool(ceiling["cleanup_complete"]),
            "ceiling_forget_errors": ceiling["errors"],
            "fullcontext_truncated": ceiling["fullcontext_truncated"],
            "longest_conversation_chars": ceiling["conversation_chars"]}
        result["controls"] = H.evaluate_controls(cfg, arms)
        result["band"] = H.evaluate_band(cfg, arms)
        result["latency"] = {**llm.stats(), **cache.stats()}
    else:
        result["qa"] = {"n": 0, "arms": {}, "why": "retrieval-only run"}
        result["controls"] = {"all_passed": None, "why": "retrieval-only run: controls not evaluated"}
        result["band"] = {"passed": None, "why": "retrieval-only run: band not evaluated"}
        result["latency"] = cache.stats()

    result["run"] = {"duration_s": round(time.time() - t0, 1),
                     "python": platform.python_version(), "platform": platform.platform(),
                     "retrieval_only": bool(args.retrieval_only),
                     "subset_spec": spec, "liveness": liveness}
    result["operating_point"] = {k: v for k, v in cfg.items() if not k.startswith("_")}
    result["operating_point"]["prompts"] = H.prompt_fingerprints()
    return result, qa_rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--subset", default="small", help="a key of config.json:subsets (small | full)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--data", default=None,
                    help=f"path to locomo10.json (else ${H.DATASET_ENV}, else benchmarks/locomo/data/)")
    ap.add_argument("--allow-dataset-drift", action="store_true",
                    help="run on a dataset whose sha256 differs from the pin (stamps dataset_drift)")
    ap.add_argument("--allow-shared-gpu", action="store_true",
                    help="override the hard GPU pre-flight; stamps gpu.contended on the result")
    ap.add_argument("--retrieval-only", action="store_true",
                    help="skip every LLM call: retrieval recall only (no GPU inference beyond cached "
                         "embeddings)")
    ap.add_argument("--skip-judge-gate", action="store_true",
                    help="do not re-run judge calibration (it is re-used from results/ if present)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="overwrite results/<subset>.json with this run instead of comparing to it")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    cfg = H.load_config(a.config)
    if a.subset not in cfg["subsets"]:
        print(f"unknown subset {a.subset!r}; known: {sorted(cfg['subsets'])}")
        return 1

    print(f"LOCOMO end-to-end · subset={a.subset} · config v{cfg['config_version']}")

    try:
        path = H.resolve_dataset(cfg, a.data)
        ds = H.check_dataset_sha(cfg, path, a.allow_dataset_drift)
    except H.DatasetMissing as e:
        print("\nSKIPPED — " + str(e))
        return 3
    print(f"  dataset {path}\n          sha256 {ds['sha256'][:16]}… "
          f"{'matches pin' if ds['matches_pin'] else 'DRIFT (allowed)'}")

    try:
        gpu = H.gpu_preflight(cfg, a.allow_shared_gpu or a.retrieval_only)
    except H.GpuBusy as e:
        print("\n" + str(e))
        return 4
    print(f"  gpu     free={gpu['free_vram_mb']} MiB of {gpu['total_vram_mb']} "
          f"contended={gpu['contended']} override={gpu['override_used']}")

    if not a.retrieval_only and not a.skip_judge_gate:
        print("\n-- judge calibration gate --")
        # Pass the dataset flags through. Without them a run started with --data would pass the gate
        # only because the gate could not find the dataset and skipped.
        jc_argv = []
        if a.config:
            jc_argv += ["--config", a.config]
        if a.data:
            jc_argv += ["--data", a.data]
        if a.allow_dataset_drift:
            jc_argv += ["--allow-dataset-drift"]
        rc = JC.main(jc_argv)
        if rc != 0:
            print("\nVOID — the judge did not clear its gate; no LOCOMO number is produced.")
            return 2 if rc == 1 else rc

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    gpu_start = H.gpu_sample(cfg)
    result, rows = run(cfg, data, a.subset, a)
    result["dataset"] = ds
    result["dataset_drift"] = not ds["matches_pin"]
    result["gpu"] = gpu
    result["gpu_window"] = H.gpu_window(cfg, gpu_start, H.gpu_sample(cfg))
    cal_path = os.path.join(H.RESULTS_DIR, "judge_calibration.json")
    if os.path.exists(cal_path) and not a.retrieval_only:
        with open(cal_path, encoding="utf-8") as fh:
            cal = json.load(fh)
        # A re-used calibration only speaks for the judge it actually tested. If the judge prompt or
        # the judge model has changed since, the stamped gate is about a different judge -- exactly the
        # shape where a check that never saw its target reports SAFE. --skip-judge-gate must not be a
        # way to inherit someone else's pass.
        cur = H.prompt_fingerprints()["judge_prompt_sha256"]
        was = ((cal.get("prompts") or {}).get("judge_prompt_sha256"))
        stale = [w for w, ok in (("judge prompt changed since calibration", was == cur),
                                 ("judge model changed since calibration",
                                  cal.get("judge_model") == cfg["qa"]["judge_model"])) if not ok]
        result["judge_calibration"] = {"gate_passed": cal.get("gate_passed"),
                                       "judge_model": cal.get("judge_model"),
                                       "judge_prompt_sha256": was,
                                       "applies_to_this_run": not stale,
                                       "stale_because": stale,
                                       "arms": {k: {"correct": v.get("correct"), "n": v.get("n"),
                                                    "rate": v.get("rate")}
                                                for k, v in (cal.get("arms") or {}).items()}}
        if stale:
            print(f"\nVOID — the re-used judge calibration does not apply to this run: {stale}. "
                  f"Re-run benchmarks/locomo/judge_calibration.py.")
            return 2
        if cal.get("gate_passed") is not True:
            print("\nVOID — the stamped judge calibration did not pass its gate.")
            return 2

    os.makedirs(H.RESULTS_DIR, exist_ok=True)
    if rows:
        with open(os.path.join(H.RESULTS_DIR, f"{a.subset}_rows.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, ensure_ascii=False)

    baseline_path = a.out or os.path.join(H.RESULTS_DIR, f"{a.subset}.json")
    exit_code = 0

    if a.update_baseline:
        if result.get("dataset_drift"):
            print("\nREFUSED to update the baseline: this run used a dataset that is not the pinned one.")
            return 1
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, ensure_ascii=False)
        print(f"\nbaseline WRITTEN -> {baseline_path}")
    else:
        latest = os.path.join(H.RESULTS_DIR, f"{a.subset}.latest.json")
        with open(latest, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, ensure_ascii=False)
        if os.path.exists(baseline_path):
            with open(baseline_path, encoding="utf-8") as fh:
                base = json.load(fh)
            cmp = H.compare_to_baseline(base, result, cfg["tolerance"])
            result["reproduction"] = cmp
            with open(latest, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=1, ensure_ascii=False)
            print(f"\n-- reproduction vs {os.path.basename(baseline_path)} --")
            for d in cmp["fields"]:
                mark = "ok " if d["within"] else "OUT"
                print(f"  [{mark}] {d['field']:44} baseline={d['baseline']} current={d['current']} "
                      f"delta={d['delta']} tol={d['tolerance']}")
            print(f"  {cmp['n_checked'] - cmp['n_outside']}/{cmp['n_checked']} within tolerance")
            if not cmp["within_tolerance"]:
                exit_code = 1
        else:
            print(f"\nno committed baseline at {baseline_path} — run with --update-baseline to create it")
            exit_code = 1

    print("\n== SUMMARY ==")
    for arm, got in sorted((result.get("retrieval") or {}).items()):
        if isinstance(got, dict) and "recall_any" in got:
            print(f"  retrieval {arm:18} recall_any@{got['k']}={got['recall_any']} "
                  f"recall_all@{got['k']}={got['recall_all']}  n={got['n']}")
    pub = (result.get("retrieval") or {}).get("published_comparison") or {}
    for arm, row in sorted((pub.get("arms") or {}).items()):
        print(f"  vs published 0.783/0.648 [{arm}]: any {row['recall_any_delta']:+.4f} / "
              f"all {row['recall_all_delta']:+.4f} -> "
              f"{'MATCHES' if row['matches_published'] else 'DOES NOT MATCH'} "
              f"(same denominator: {row['same_denominator_as_published']})")
    for arm, got in sorted(((result.get("qa") or {}).get("arms") or {}).items()):
        print(f"  qa        {arm:18} accuracy={got['accuracy']} ({got['correct']}/{got['n']})")

    controls = result.get("controls") or {}
    band = result.get("band") or {}
    lat = result.get("latency") or {}
    if lat.get("llm_errors"):
        exit_code = 1
        print(f"\n  {lat['llm_errors']} model call(s) FAILED after retries — every failure was scored "
              f"as a wrong answer, so this run understates every arm. Do not publish it.")
    if lat.get("suspected_cache_hits"):
        print(f"  NOTE {lat['suspected_cache_hits']} repl(ies) returned in under "
              f"{lat['cache_hit_threshold_s']}s — a 0.0s reply is a cache hit, not a call.")
    win = result.get("gpu_window") or {}
    if win.get("stable") is False:
        exit_code = 1
        print("\n  THE CARD CHANGED UNDERNEATH THIS RUN — it was not one measurement regime:")
        for d in win["drift"]:
            print(f"    {d}")
        print("    Every wall-clock number here spans both regimes. Re-run before quoting it.")
    elif win.get("stable"):
        print(f"  gpu stable across the run: {win['start']['free_vram_mb']} -> "
              f"{win['end']['free_vram_mb']} MiB free, {len(win['end']['foreign_runners'])} foreign runner(s)")
    if (result.get("qa") or {}).get("ceiling_cleanup_complete") is False:
        exit_code = 1
        print(f"  ceiling control records were NOT fully erased "
              f"({result['qa']['ceiling_records_forgotten']}/{result['qa']['ceiling_records_written']}) "
              f"— {result['qa']['ceiling_forget_errors']}")
    if controls.get("all_passed") is False:
        exit_code = 1
        print("\n  HARNESS BROKEN — a control failed, so no number here should be published:")
        for k, v in controls.items():
            if isinstance(v, dict) and not v["passed"]:
                print(f"    {k}: {v['why']}")
    elif controls.get("all_passed"):
        print("\n  controls PASSED (floors near chance, verbatim ceiling near 1.0)")
    if band.get("passed") is False:
        exit_code = 1
        print(f"  BAND FAILED — {band['why']}")
    elif band.get("passed"):
        print(f"  band PASSED — {band['arms']['floor_arm']} {band['accuracy']['floor']} < inspeximus "
              f"{band['accuracy']['subject']} < {band['arms']['ceiling_arm']} {band['accuracy']['ceiling']}")

    print(f"\nexit={exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
