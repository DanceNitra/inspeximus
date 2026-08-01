"""Deterministic corpus for the cross-system parity harness.

One corpus, five axes. Nothing is downloaded and no external dataset is required, so every cell in the
published table can be reproduced from this file plus a seed. The generated JSON is committed under
`corpus/` so a reader can inspect the exact fixture the numbers came from without trusting the generator.

Design constraints, all of them deliberate (see PREREGISTRATION.md):

* **Probes are paraphrased away from the stored wording.** A probe and the fact it targets share no content
  word. That is the condition under which a lexical retriever is expected to LOSE to an embedding one, and
  it was chosen because it is the condition that is bad for inspeximus's zero-dependency default. A corpus
  whose questions repeat the stored sentence would have handed us the retrieval row, which is exactly the
  kind of home-fixture result a serious reader takes apart first.
* **Values never collide.** `value_a` / `value_b` are drawn from a curated pool of short distinct tokens and
  are checked to be non-substrings of each other and absent from every other string in the thread. A
  substring collision would silently turn a correct answer into an "unclear" verdict.
* **Each thread is independent** and gets its own namespace in every arm (own store dir / user_id /
  group_id / agent), so nothing leaks between scenarios and threads can run in any order.
"""
from __future__ import annotations

import json
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
SEED = 20260801

# (entity, value_a, value_b) — short, distinct, non-substring tokens. Pool reused from
# probes/integrity_bench_revert.py so the two harnesses share a fixture vocabulary rather than
# inventing a second one.
ENTITIES = [
    ("cache region", "osaka", "malmo"), ("primary shard", "delta7", "sigma2"),
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
    ("tls version", "tls12", "tls13"), ("node runtime", "bun", "deno"),
]

# (stored fact phrasing, paraphrased probe) — the two sides share NO content word. This is the whole
# retrieval axis: a lexical retriever has nothing to match on except the value token, which the probe
# does not contain, so it must generalise or miss.
FACT_PROBE = [
    ("the deployment region for {t} is {v}", "where does {t} physically run?"),
    ("the payroll currency for {t} is {v}", "how are {t} salaries denominated?"),
    ("the escalation contact for {t} is {v}", "who gets paged when {t} breaks at night?"),
    ("the retention window for {t} is {v}", "how long do we keep {t} records before disposal?"),
    ("the onboarding checklist for {t} is {v}", "what does a new {t} joiner have to complete?"),
    ("the incident severity floor for {t} is {v}", "at what level does a {t} outage become reportable?"),
    ("the procurement vendor for {t} is {v}", "who supplies {t} hardware?"),
    ("the audit cadence for {t} is {v}", "how often is {t} inspected?"),
    ("the failover partner for {t} is {v}", "which site takes over if {t} goes dark?"),
    ("the encryption profile for {t} is {v}", "how is {t} data protected at rest?"),
    ("the budget owner for {t} is {v}", "who signs off {t} spending?"),
    ("the release train for {t} is {v}", "on what schedule does {t} ship?"),
    ("the data residency for {t} is {v}", "in which jurisdiction must {t} records stay?"),
    ("the support tier for {t} is {v}", "what response time do {t} customers get?"),
    ("the archive medium for {t} is {v}", "what do we write {t} cold copies onto?"),
    ("the review board for {t} is {v}", "which committee approves {t} changes?"),
    ("the training corpus for {t} is {v}", "what material was {t} learned from?"),
    ("the migration wave for {t} is {v}", "in which batch does {t} move?"),
    ("the licence class for {t} is {v}", "under what terms may {t} be redistributed?"),
    ("the calibration standard for {t} is {v}", "against what reference is {t} checked?"),
    ("the pseudonym scheme for {t} is {v}", "how are {t} identifiers masked?"),
    ("the freight carrier for {t} is {v}", "who ships {t} goods?"),
    ("the disaster drill for {t} is {v}", "what rehearsal covers a {t} catastrophe?"),
    ("the credential rotation for {t} is {v}", "how often are {t} secrets replaced?"),
    ("the anomaly threshold for {t} is {v}", "when does {t} behaviour count as abnormal?"),
    ("the settlement bank for {t} is {v}", "where does {t} money clear?"),
    ("the accessibility target for {t} is {v}", "what standard must {t} meet for disabled users?"),
    ("the sampling rate for {t} is {v}", "how densely is {t} observed?"),
    ("the appeals route for {t} is {v}", "how does someone contest a {t} decision?"),
    ("the sunset date for {t} is {v}", "when is {t} withdrawn from service?"),
    ("the insurance underwriter for {t} is {v}", "who carries the {t} risk?"),
    ("the translation memory for {t} is {v}", "what resource supports {t} localisation?"),
    ("the quorum size for {t} is {v}", "how many participants must agree for {t}?"),
    ("the provenance ledger for {t} is {v}", "where is the {t} chain of custody written?"),
    ("the fallback locale for {t} is {v}", "what language does {t} use when none is set?"),
    ("the physical custodian for {t} is {v}", "who holds the {t} keys in the building?"),
    ("the depreciation schedule for {t} is {v}", "over what period is {t} written down?"),
    ("the peer reviewer for {t} is {v}", "who independently checks {t} work?"),
    ("the emission factor for {t} is {v}", "what carbon coefficient applies to {t}?"),
    ("the naming convention for {t} is {v}", "how are {t} objects titled?"),
]

_STEM_C = "bdfgklmnprstvz"
_STEM_V = "aeiou"


def _token(rng: random.Random) -> str:
    """A pronounceable, unique-by-construction filler value. Fixed shape (CVCCVC + 3 digits) so no
    generated value can ever be a substring of another one, which would corrupt the verdict function."""
    return (rng.choice(_STEM_C) + rng.choice(_STEM_V) + rng.choice(_STEM_C)
            + rng.choice(_STEM_C) + rng.choice(_STEM_V) + rng.choice(_STEM_C)
            + f"{rng.randrange(100, 1000)}")


def build(threads: int, distractors: int, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    if threads > len(ENTITIES):
        raise ValueError(f"corpus supports at most {len(ENTITIES)} threads (entity pool size)")
    if distractors > len(FACT_PROBE):
        raise ValueError(f"corpus supports at most {len(FACT_PROBE)} distractors per thread")
    out = []
    used: set[str] = set()
    for i in range(threads):
        entity, va, vb = ENTITIES[i]
        tag = f"team{i:02d}"
        facts, probes = [], []
        for j in range(distractors):
            tmpl, probe = FACT_PROBE[j]
            while True:
                v = _token(rng)
                if v not in used:
                    used.add(v)
                    break
            facts.append({"text": tmpl.format(t=tag, v=v), "value": v})
            probes.append({"question": probe.format(t=tag), "gold": v})
        secret = f"AKT-{rng.randrange(1000, 9999)}-{rng.choice('KLMNPQRS')}{rng.choice('TUVWXYZ')}"
        retained = []
        for label in ("home office", "cost centre", "duty roster"):
            while True:
                v = _token(rng)
                if v not in used:
                    used.add(v)
                    break
            retained.append({"text": f"the {label} for {tag} is {v}", "value": v,
                             "question": f"what {label} applies to {tag}?"})
        out.append({
            "id": f"t{i:02d}", "tag": tag, "entity": entity, "key": f"{tag}:{entity}",
            "value_a": va, "value_b": vb,
            "assert_a": f"the {entity} for {tag} is {va}",
            "correct_b": f"correction: the {entity} for {tag} is now {vb}",
            "revert_utterance": f"go back to what we had for the {entity} of {tag}",
            "echo_utterance": f"the {entity} for {tag} is {va}",
            "read_query": f"what is the current {entity} for {tag}?",
            "secret": secret,
            "secret_text": f"the private account reference for {tag} is {secret}",
            "secret_question": f"what is the private account reference for {tag}?",
            "retained": retained,
            "distractors": facts,
            "probes": probes,
        })
    _validate(out)
    return {"seed": seed, "threads": threads, "distractors_per_thread": distractors,
            "generator": "benchmarks/parity/corpus.py", "items": out}


def _validate(threads: list[dict]) -> None:
    """A corpus defect is indistinguishable from a system defect once the numbers exist, so the fixture
    asserts its own invariants rather than trusting the generator. Every failure here was a real risk:
    a value that is a substring of another turns a correct read into `unclear`, and a probe that shares a
    word with its own fact turns the paraphrase axis back into a keyword-match axis."""
    for th in threads:
        a, b = th["value_a"], th["value_b"]
        assert a != b and a not in b and b not in a, f"{th['id']}: value collision {a!r}/{b!r}"
        blob = " ".join([d["text"] for d in th["distractors"]]
                        + [r["text"] for r in th["retained"]] + [th["secret_text"]]).lower()
        assert a not in blob and b not in blob, f"{th['id']}: A/B leaks into the haystack"
        vals = [d["value"] for d in th["distractors"]] + [r["value"] for r in th["retained"]]
        assert len(set(vals)) == len(vals), f"{th['id']}: duplicate filler value"
        for d, p in zip(th["distractors"], th["probes"]):
            fact_words = {w.strip("?.,") for w in d["text"].lower().split()}
            probe_words = {w.strip("?.,") for w in p["question"].lower().split()}
            overlap = (fact_words & probe_words) - _STOP - {th["tag"]}
            assert not overlap, f"{th['id']}: probe shares {overlap} with its fact — not a paraphrase"
        assert th["secret"] not in blob.replace(th["secret_text"].lower(), ""), "secret leaks elsewhere"


_STOP = {"the", "a", "an", "is", "are", "for", "of", "to", "in", "on", "at", "does", "do", "what",
         "which", "who", "how", "when", "where", "must", "may", "and", "it", "that", "from", "into",
         "by", "with", "under", "over", "before", "after", "was", "were", "be", "been", "against"}

SUBSETS = {"small": (12, 8), "full": (50, 40)}


def load(subset: str) -> dict:
    """Prefer the committed JSON (that is the fixture the published numbers came from); regenerate only
    if it is absent. Measure the corpus that SHIPS, not the one the generator would make today."""
    p = HERE / "corpus" / f"{subset}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    threads, distractors = SUBSETS[subset]
    return build(threads, distractors)


def main() -> None:
    (HERE / "corpus").mkdir(exist_ok=True)
    for name, (threads, distractors) in SUBSETS.items():
        c = build(threads, distractors)
        (HERE / "corpus" / f"{name}.json").write_text(
            json.dumps(c, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{name}: {threads} threads x {distractors} distractors "
              f"-> {threads * (distractors + 6)} writes, {threads * distractors} probes")


if __name__ == "__main__":
    main()
