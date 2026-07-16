"""
MemoryAgentBench — Conflict Resolution (FactConsolidation) external benchmark, mnemo vs accumulate baseline.

Why this bench: MemoryAgentBench (Hu et al., arXiv:2507.05257) reports that on the Conflict-Resolution axis
EVERY evaluated memory system (mem0, MemGPT, Cognee, BM25 RAG, full-context GPT-4o) scores single digits
(<=7%). CR = "update outdated information instead of accumulating", which is exactly mnemo's supersession.
This is neutral, published, external ground, not a self-authored probe.

Task (FactConsolidation, multi-hop, ~6k context): a numbered stream of facts where the SAME (subject,
relation) is later RESTATED with a new value; a multi-hop question asks for the CURRENT (latest) value.
An accumulate store surfaces the stale value and the LLM chains the wrong fact.

Fairness: SAME LLM for both arms (Ollama Cloud), the ONLY difference is the memory layer:
  - baseline "accumulate": the LLM sees ALL fact lines (stale + updated).
  - mnemo "consolidate": each parseable fact is remember()'d with key=(subject|relation); a later
    restatement supersedes the earlier, so the LLM sees only the LATEST value per key.
We report the baseline number too, so the <=7% claim is reproduced under our protocol, not assumed.
"""
import re

# Closed-set relation templates -> (key, value). Key = (entity, relation-slug); value = the object.
# Order matters (more specific first). Unmatched facts pass through verbatim (no conflict resolution).
_TEMPLATES = [
    (r"^(.+?) was born in the city of (.+)$", "born_city"),
    (r"^(.+?) died in the city of (.+)$", "died_city"),
    (r"^(.+?) plays the position of (.+)$", "position"),
    (r"^(.+?) is located in the continent of (.+)$", "continent"),
    (r"^(.+?) is associated with the sport of (.+)$", "sport"),
    (r"^(.+?) is married to (.+)$", "spouse"),
    (r"^(.+?) is a citizen of (.+)$", "citizen"),
    (r"^(.+?) worked in the city of (.+)$", "worked_city"),
    (r"^The headquarters of (.+?) is located in the city of (.+)$", "hq_city"),
    (r"^The name of the current head of state in (.+?) is (.+)$", "head_of_state"),
    (r"^The name of the current head of the (.+?) government is (.+)$", "head_of_gov"),
    (r"^The chairperson of (.+?) is (.+)$", "chairperson"),
    (r"^The author of (.+?) is (.+)$", "author"),
    (r"^The director of (.+?) is (.+)$", "director"),
    (r"^The capital of (.+?) is (.+)$", "capital"),
    (r"^The univer\w+ where (.+?) was educated is (.+)$", "educated_at"),
    (r"^The chief executive officer of (.+?) is (.+)$", "ceo"),
    (r"^The official language of (.+?) is (.+)$", "language"),
    (r"^(.+?) was founded by (.+)$", "founded_by"),
    (r"^(.+?) was performed by (.+)$", "performer"),
    (r"^(.+?) is affiliated with the religion of (.+)$", "religion"),
    (r"^(.+?)'s child is (.+)$", "child"),
    (r"^(.+?) was founded in the city of (.+)$", "founded_city"),
    (r"^(.+?) was created in the country of (.+)$", "created_country"),
    (r"^(.+?) is employed by (.+)$", "employer"),
    (r"^(.+?) was developed by (.+)$", "developer"),
    (r"^(.+?) was written in the language of (.+)$", "written_language"),
    (r"^(.+?) was created by (.+)$", "creator"),
    (r"^The type of music that (.+?) plays is (.+)$", "music_type"),
    (r"^(.+?) speaks the language of (.+)$", "speaks_language"),
    (r"^The country of citizenship of (.+?) is (.+)$", "citizen"),
    (r"^The place of birth of (.+?) is (.+)$", "born_city"),
    (r"^The position played by (.+?) is (.+)$", "position"),
    (r"^The sport associated with (.+?) is (.+)$", "sport"),
    (r"^The spouse of (.+?) is (.+)$", "spouse"),
]
_COMPILED = [(re.compile(p), slug) for p, slug in _TEMPLATES]


def parse_fact(line: str):
    """Return (key, value, relation) if the fact matches a known template, else None."""
    for rx, slug in _COMPILED:
        m = rx.match(line)
        if m:
            entity, value = m.group(1).strip(), m.group(2).strip().rstrip(".")
            return f"{entity.lower()}||{slug}", value, slug
    return None


def fact_lines(context: str):
    return [re.sub(r"^\d+\.\s*", "", l).strip().rstrip(".")
            for l in context.split("\n") if re.match(r"^\d+\.\s", l)]


def consolidate_with_mnemo(lines):
    """Feed facts into mnemo in stream order; a later restatement of the same (entity|relation) key
    supersedes the earlier. Return the ACTIVE consolidated fact list (latest per key + unmatched)."""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from mnemo import Mnemo
    st = Mnemo(None)                       # no embedder needed: keyed supersession, not semantic recall
    passthrough = []
    for ln in lines:
        p = parse_fact(ln)
        if p is None:
            passthrough.append(ln)
            continue
        key, value, slug = p
        st.remember(ln, key=key, object=value)   # keyed write -> auto-supersede on repeat key
    active = [r["text"] for r in st.items if r.get("status") == "active"]
    return active + passthrough, st


_SYS = ("You answer a question using ONLY the facts provided. Some facts are updated later in the list: "
        "when the same thing is stated more than once, ALWAYS use the MOST RECENT (latest-listed) value. "
        "Reason step by step internally but reply with ONLY the final answer, no explanation.")


def _llm(facts, question):
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "agora", "server"))
    from agora.execution.llm_client import call_llm
    usr = "Facts:\n" + "\n".join(facts) + f"\n\nQuestion: {question}\nAnswer:"
    return call_llm(_SYS, usr, tier="cheap", temperature=0.0, max_tokens=40) or ""


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower()).strip()


def _correct(output, gold_list):
    o = _norm(output)
    return any(_norm(g) and _norm(g) in o for g in gold_list)


def run_eval(row, n_questions=25, k=30):
    lines = fact_lines(row["context"])
    consolidated, st = consolidate_with_mnemo(lines)      # st: mnemo store, keyed-superseded
    # a RAW-accumulate store (all facts, no supersession) for a same-retrieval fair contrast
    from mnemo import Mnemo
    raw = Mnemo(None)
    for ln in lines:
        raw.remember(ln)
    qs = list(row["questions"])[:n_questions]
    golds = list(row["answers"])[:n_questions]
    hits = {"base_full": 0, "mnemo_full": 0, "raw_retr": 0, "mnemo_retr": 0}
    for q, gold in zip(qs, golds):
        gl = list(gold) if hasattr(gold, "__len__") and not isinstance(gold, str) else [gold]
        # full-context arms
        if _correct(_llm(lines, q), gl):
            hits["base_full"] += 1
        if _correct(_llm(consolidated, q), gl):
            hits["mnemo_full"] += 1
        # retrieval arms: lexical top-k, SAME retriever; only supersession differs
        raw_hits = [h["text"] for h in raw.recall(q, k=k, mode="lexical")]
        mn_hits = [h["text"] for h in st.recall(q, k=k, mode="lexical")]
        if _correct(_llm(raw_hits, q), gl):
            hits["raw_retr"] += 1
        if _correct(_llm(mn_hits, q), gl):
            hits["mnemo_retr"] += 1
    n = len(qs)
    return {"n": n, "facts": len(lines), "consolidated": len(consolidated), "k": k,
            **{f"{key}_acc": round(v / n, 3) for key, v in hits.items()}}


if __name__ == "__main__":
    from huggingface_hub import hf_hub_download
    import pandas as pd
    p = hf_hub_download("ai-hyz/MemoryAgentBench", "data/Conflict_Resolution-00000-of-00001.parquet",
                        repo_type="dataset")
    df = pd.read_parquet(p)
    tot_facts = tot_active = tot_conflicts = tot_unmatched = 0
    for i, row in df.iterrows():
        lines = fact_lines(row["context"])
        # count conflicts = keys seen 2+ times
        from collections import Counter
        keys = Counter()
        unmatched = 0
        for ln in lines:
            pr = parse_fact(ln)
            if pr is None:
                unmatched += 1
            else:
                keys[pr[0]] += 1
        conflicts = sum(1 for k, n in keys.items() if n > 1)
        active, st = consolidate_with_mnemo(lines)
        tot_facts += len(lines); tot_active += len(active)
        tot_conflicts += conflicts; tot_unmatched += unmatched
        print(f"row {i}: facts={len(lines)} matched_keys={len(keys)} conflicts={conflicts} "
              f"unmatched={unmatched} -> consolidated={len(active)} (removed {len(lines)-len(active)} stale)")
    print(f"\nTOTAL: {tot_facts} facts, {tot_conflicts} conflicts resolved, {tot_unmatched} unmatched "
          f"({100*tot_unmatched/tot_facts:.1f}% pass-through), consolidated to {tot_active}.")
