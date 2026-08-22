"""integrity_bench_revert.py — cross-system INTEGRITY benchmark, dimension 1: value-obscuring revert.

The skeptic's challenge (r/RAG storm, 2026-07-11): don't self-score on home fixtures — run the integrity
tests against mem0 and Zep/Graphiti and publish whoever wins. This is that, falsifiable, native config each.

TASK (value-obscuring revert): store a value, correct it, then issue an UNMARKED revert that names no value
("go back to what we had", "revert that last change", "undo it"). Does the system's current answer return to
the OLD value? This is the one integrity operation no recall benchmark tests, and the storm's own converged
finding is that it can't be done from text alone without an explicit revert channel.

  sequence per case:
    1. add   "the {entity} is {A}."
    2. add   "correction: the {entity} is now {B}."
    3. revert "{unmarked revert utterance, no value}"
    4. ask   "what is the current {entity}?"  ->  A = revert honored, B = revert ignored, other = confused

Systems (native config, no tuning in our favor):
  - inspeximus   : route() the revert (its intent router + ledger revert), then recall. Local, no LLM.
  - mem0    : Memory() default (OpenAI gpt-4o-mini extractor + text-embedding-3-small); add() all three,
              then search() and let the SAME judge read the retrieved memories for the current value.
  - graphiti: (added in a follow-up cell; neo4j is up) — same protocol.

Metric = revert-success rate (fraction where the current answer == A). A shared OpenAI judge reads each
system's returned context and extracts the current value, so the answer step is identical across systems
(the judge never sees the ground truth). n kept small (OpenAI cost, owner-approved for competitor native
config). Deterministic fixture. RUN: python inspeximus/probes/integrity_bench_revert.py [--n 20]
"""
import os, sys, json, re, time, urllib.request, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from inspeximus import Inspeximus

def _load_key():
    """The process environment first, a dotenv only as a fallback, and never the other way round.

    Reported by @mioimotoai-lgtm on DanceNitra/inspeximus#1, from a clean clone, and every word of it
    reproduced 38 days later on 2.20.0. Three defects in seven lines:

      * `open("server/.env")` was UNCONDITIONAL and relative to the CURRENT DIRECTORY, so the command
        published in docs/CLAIMS.md died with FileNotFoundError before argparse ever ran. Not a
        missing dependency -- a path. Supplying an OpenAI key did not help;
      * it then OVERWROTE any real `OPENAI_API_KEY` already in the environment with whatever that
        file held, including the empty string;
      * the file it wanted has never been in this repository.

    Six numbers on the published site (0.75 / 0.20 / 0.00 and the counters restating them) cite that
    command as their reproduction, under a status that promises the command runs once you supply the
    dependencies. It did not run at all.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "server", ".env"),
                 os.path.join(here, "..", ".env"),
                 os.path.join(os.getcwd(), "server", ".env")):
        if not os.path.exists(cand):
            continue
        try:
            with open(cand, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "OPENAI_API_KEY":
                            return v.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


OPENAI_KEY = _load_key()
if OPENAI_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_KEY

ENTS = [("cache region", "osaka", "malmo"), ("primary shard", "delta7", "sigma2"),
        ("build target", "arm64", "riscv"), ("default currency", "forint", "guarani"),
        ("route profile", "coastal", "inland"), ("api tier", "bronze", "platinum"),
        ("index locale", "tallinn", "cusco"), ("worker pool", "amber", "cobalt"),
        ("log sink", "vault3", "harbor8"), ("retry policy", "linear", "jitter"),
        ("color theme", "sepia", "slate"), ("scheduler", "roundrobin", "weighted"),
        ("session store", "sticky", "pooled"), ("cdn provider", "fastly", "bunny"),
        ("rate limiter", "tiered", "flat"), ("search engine", "elastic", "sonic"),
        ("email sender", "postmark", "sendy"), ("backup window", "0200utc", "0400utc"),
        ("queue driver", "kafka", "nats"), ("feature flag", "canary", "stable"),
        ("dns resolver", "quad9", "opendns"), ("time source", "chrony", "ntpd"),
        ("hash algo", "blake3", "sha256"), ("compression", "zstd", "brotli"),
        ("lock manager", "redlock", "zookeeper"), ("metrics store", "prometheus", "influx"),
        ("trace backend", "jaeger", "tempo"), ("secret store", "sealed", "sops"),
        ("load balancer", "haproxy", "traefik"), ("object store", "minio", "ceph"),
        ("message format", "protobuf", "avro"), ("auth scheme", "oauth", "saml"),
        ("eviction", "clocksweep", "twoqueue"), ("db engine", "postgres", "cockroach"),
        ("orm layer", "prisma", "drizzle"), ("test runner", "pytest", "unittest"),
        ("ci system", "jenkins", "drone"), ("container runtime", "containerd", "crio"),
        ("service mesh", "istio", "linkerd"), ("api gateway", "kong", "tyk"),
        ("event bus", "rabbitmq", "pulsar"), ("feature store", "feast", "tecton"),
        ("vector db", "qdrant", "milvus"), ("graph db", "neptune", "dgraph"),
        ("stream proc", "flink", "sparkstream"), ("config format", "yaml", "toml"),
        ("license", "permissive", "copyleft"), ("home region", "frankfurt", "singapore"),
        ("tls version", "tls12", "tls13"), ("node runtime", "bun", "deno")]
REVERTS = ["go back to what we had for the {e}.", "revert that last {e} change.", "undo the {e} correction.",
           "put the {e} back the way it was.", "roll back the {e} change."]


def openai_chat(prompt, model="gpt-4o-mini", temp=0.0):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": temp, "max_tokens": 60}).encode()
    for a in range(6):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "https://api.openai.com/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}),
                timeout=60)
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if a == 5:
                return None
            time.sleep(3 * (a + 1))   # linear-growing backoff; rate limits accumulate on long runs


def wilson(k, n, z=1.96):
    """Wilson 95% CI for a binomial rate k/n (small-n honest interval)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (round((c - h) / d, 3), round((c + h) / d, 3))


JUDGE = "openai"          # set once in main(); every system is read through whichever is selected


def judge_local(entity, context_text, A, B):
    """A DIFFERENT INSTRUMENT, not a cheaper version of the same one. Deliberately labelled.

    The second half of #1: with the shared judge the "inspeximus only" run is not free, because
    `run_inspeximus` goes through the same paid judge as its competitors -- which is correct and was
    a deliberate fairness fix, since scoring inspeximus mechanically off its own ledger while reading
    the others through an LLM is an asymmetric instrument. That fix is not being undone here.

    So this is offered as a separate judge, applied IDENTICALLY to every system, and its verdicts
    must never be mixed into a table with `openai` verdicts. The rule, stated rather than buried:
    exactly one candidate present in the retrieved context wins; both present, or neither, is
    `other`. What it measures cleanly is whether the stale value is still in the retrieved context
    at all.

    THE DIRECTION OF THE DIFFERENCE IS NOT PREDICTABLE, and the first version of this docstring got
    it wrong in a way worth leaving on the record. It reasoned that reading strictly must score
    LOWER, since a store returning the correction beside the superseded value lands on `other` where
    a language model resolves it. Measured on the same fixture, n=20, inspeximus scores **1.00 here
    against the published 0.75** -- higher, because after a safe-policy revert its recall surface
    holds one candidate and the deterministic rule then has nothing to be unsure about, while the
    LLM judge answered otherwise on five of twenty. A rule that removes judgement removes it in both
    directions. That gap, 0.25 on one system, is the concrete reason these two columns never belong
    in one table.
    """
    t = (context_text or "").lower()
    a_in, b_in = A.lower() in t, B.lower() in t
    if a_in and not b_in:
        return "A"
    if b_in and not a_in:
        return "B"
    return "other"


def judge_current(entity, context_text, A, B):
    """Shared judge: given a system's retrieved memories, what is the CURRENT value? Never sees ground truth
    beyond the two candidate tokens (both provided so the judge can also say 'unclear')."""
    if JUDGE == "local":
        return judge_local(entity, context_text, A, B)
    prompt = (f"Here are an agent's stored memories about \"{entity}\":\n---\n{context_text}\n---\n"
              f"Based only on these, what is the CURRENT value of the {entity} right now? "
              f"Answer with exactly one word: the value, or 'unclear'.")
    ans = (openai_chat(prompt) or "").lower()
    if A in ans and B not in ans:
        return "A"
    if B in ans and A not in ans:
        return "B"
    return "other"


# ── adapters: common interface (reset, add, revert, context_for_judge) ───────
def run_inspeximus(cases):
    res = []
    for (e, A, B, rev) in cases:
        m = Inspeximus(path=None); m.echo_guard = True
        m.remember(f"the {e} is {A}", key=e, object=A)
        m.remember(f"correction: the {e} is now {B}", key=e, object=B)
        m.route(rev, policy="safe")                      # its revert router (no LLM); safe policy
        hits = m.recall(e, k=6)
        ctx = "\n".join(h["text"] for h in hits)
        # SYMMETRIC INSTRUMENT (fairness fix 2026-07-11): read inspeximus's current value through the SAME LLM
        # judge on its native recall surface, exactly as mem0/graphiti are read. The earlier version scored
        # inspeximus mechanically from its own ledger while competitors went through the judge — an asymmetric
        # instrument that confounded the comparison (caught by the pre-publication stress-claim audit).
        res.append(judge_current(e, ctx or "(no memories)", A, B))
    return res


def run_mem0(cases):
    from mem0 import Memory
    # ONE Memory instance (avoids the multi-instance Qdrant lock); isolate cases by user_id. Explicit native
    # config: gpt-4o-mini (supports the extractor temperature the default 2.0.11 model rejected) + the
    # recommended embedder. This is mem0's own recommended stack, no tuning in our favor.
    cfg = {"llm": {"provider": "openai", "config": {"model": "gpt-4o-mini", "temperature": 0.1}},
           "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}}}
    try:
        mem = Memory.from_config(cfg)
    except Exception as ex:
        print(f"    [mem0 init FAILED: {str(ex)[:120]}]", flush=True)
        return ["error"] * len(cases)
    res = []
    for i, (e, A, B, rev) in enumerate(cases):
        try:
            uid = f"case{i}"
            mem.add(f"the {e} is {A}", user_id=uid)
            mem.add(f"correction: the {e} is now {B}", user_id=uid)
            mem.add(rev, user_id=uid)
            # give the judge the FULL memory state (get_all), not just search top-k — isolates the integrity
            # question (did the revert change the state) from retrieval quality (a different axis, not tested).
            ga = mem.get_all(filters={"user_id": uid}, top_k=30)
            mems = ga.get("results", ga) if isinstance(ga, dict) else ga
            ctx = "\n".join((x.get("memory") or x.get("text") or str(x)) for x in (mems or []))
            res.append(judge_current(e, ctx or "(no memories)", A, B))
        except Exception as ex:
            print(f"    [mem0 case {i} error: {str(ex)[:90]}]", flush=True)
            res.append("error")
        if (i + 1) % 5 == 0:
            print(f"    mem0 {i+1}/{len(cases)}", flush=True)
    return res


def run_graphiti(cases):
    """Graphiti (native, live neo4j + OpenAI). Each case = its own group_id; three episodes at increasing
    reference_time; then search the current value and judge on the returned (valid, non-invalidated) facts."""
    import asyncio, datetime
    from graphiti_core import Graphiti
    from graphiti_core.nodes import EpisodeType

    async def _run():
        g = Graphiti("bolt://localhost:7687", "neo4j", "testpassword123")
        out = []
        try:
            await g.build_indices_and_constraints()
            for i, (e, A, B, rev) in enumerate(cases):
                try:
                    gid = f"revcase_{i}_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S%f')}"
                    t0 = datetime.datetime(2026, 7, 11, 10, 0, 0, tzinfo=datetime.timezone.utc)
                    for j, msg in enumerate([f"the {e} is {A}", f"correction: the {e} is now {B}", rev]):
                        await g.add_episode(name=f"m{j}", episode_body=msg, source_description="chat",
                                            reference_time=t0 + datetime.timedelta(minutes=j),
                                            source=EpisodeType.message, group_id=gid)
                    res = await g.search(f"what is the current {e}?", group_ids=[gid], num_results=10)
                    ctx = "\n".join(getattr(x, "fact", str(x)) for x in res)
                    out.append(judge_current(e, ctx or "(no facts)", A, B))
                except Exception as ex:
                    print(f"    [graphiti case {i} error: {str(ex)[:90]}]", flush=True)
                    out.append("error")
                if (i + 1) % 5 == 0:
                    print(f"    graphiti {i+1}/{len(cases)}", flush=True)
        finally:
            await g.close()
        return out
    return asyncio.run(_run())


def score(name, verdicts, cases):
    A = sum(1 for v in verdicts if v == "A")
    B = sum(1 for v in verdicts if v == "B")
    o = sum(1 for v in verdicts if v == "other")
    err = sum(1 for v in verdicts if v == "error")
    n = len(cases) - err
    rate = A / n if n else 0.0
    lo, hi = wilson(A, n)
    return {"system": name, "n": n, "revert_honored_A": A, "kept_new_B": B, "other": o, "errors": err,
            "revert_success_rate": round(rate, 3), "ci95": [lo, hi]}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--systems", default="inspeximus,mem0",
                    help="comma list: inspeximus, mem0 (needs neo4j-free OpenAI $), graphiti "
                         "(OpenAI $ + neo4j). Every system is read through the SAME judge, so "
                         "'inspeximus' alone still costs OpenAI calls unless --judge local.")
    ap.add_argument("--judge", choices=("openai", "local"), default="openai",
                    help="openai: the shared LLM judge the published numbers use. "
                         "local: a deterministic, free, DIFFERENT instrument -- see judge_local. "
                         "Never compare scores across the two.")
    a = ap.parse_args()
    global JUDGE
    JUDGE = a.judge
    want = [s.strip() for s in a.systems.split(",") if s.strip()]

    # FAIL FAST AND SAY WHAT TO DO, which is the whole of #1's first ask. Before this, a missing key
    # produced either a FileNotFoundError about a path that was never in the repo, or a run that
    # burned through every case retrying unauthenticated calls and reported the failures as scores.
    if JUDGE == "openai" and not OPENAI_KEY:
        print("This benchmark reads every system through a shared OpenAI judge, and no key is set.\n"
              "  * export OPENAI_API_KEY=...   to reproduce the published numbers, or\n"
              "  * add --judge local           for a free deterministic run, which is a DIFFERENT\n"
              "                                instrument and is not comparable with them.\n"
              "A dotenv at server/.env or .env beside the repo root is read only as a fallback.",
              file=sys.stderr)
        return 2
    cases = []
    for i in range(min(a.n, len(ENTS))):
        e, A, B = ENTS[i]
        cases.append((e, A, B, REVERTS[i % len(REVERTS)].format(e=e)))
    print(f"cross-system integrity benchmark — value-obscuring revert · n={len(cases)} · systems={want}\n")
    out = {}
    if "inspeximus" in want:
        print("inspeximus (local, route/revert)...")
        out["inspeximus"] = score("inspeximus", run_inspeximus(cases), cases); print(json.dumps(out["inspeximus"]))
    if "mem0" in want:
        print("\nmem0 (native, OpenAI gpt-4o-mini)...")
        out["mem0"] = score("mem0", run_mem0(cases), cases); print(json.dumps(out["mem0"]))
    if "graphiti" in want:
        print("\ngraphiti (native, live neo4j + OpenAI)...")
        out["graphiti"] = score("graphiti", run_graphiti(cases), cases); print(json.dumps(out["graphiti"]))
    # THE JUDGE TRAVELS WITH THE NUMBER. A score whose instrument is not recorded beside it invites
    # exactly the comparison judge_local warns against, and the published file carried no such field
    # at all. A local run also writes to its own filename, so a free run cannot overwrite the
    # artifact the site's figures cite.
    stem = "integrity_bench_revert_result" + ("_localjudge" if JUDGE == "local" else "")
    json.dump({"task": "value-obscuring revert", "metric": "revert_success_rate (current answer == old value)",
               "judge": JUDGE, "comparable_with_published": JUDGE == "openai",
               "results": out}, open(os.path.join(os.path.dirname(__file__), stem + ".json"), "w"),
              indent=2)
    if JUDGE == "local":
        print("\n[judge=local] deterministic instrument, free to run, NOT comparable with the "
              "published openai-judged figures. See judge_local for the rule and its bias.")
    print("\n=== MATRIX (revert success: does an unmarked 'go back' restore the old value?) ===")
    for k, v in out.items():
        print(f"  {k:9s} {v['revert_success_rate']:.2f}  (A={v['revert_honored_A']} B={v['kept_new_B']} other={v['other']} err={v['errors']}, n={v['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
