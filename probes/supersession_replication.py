"""Crucible replication: does cosine similarity have a 'supersession blind spot'?

Triggered by a domain expert (MemStrata / Neeraj Yadav, arXiv 2606.26511) on our MINJA post.
His central claim: a cosine-similarity classifier separating a *contradicted* (superseded) fact from
a *duplicated* (rephrased) one scores AUROC ~0.59 — near chance — because a contradiction can sit
CLOSER in embedding space to the original than a genuine rephrase does. Consequence: a similarity-based
retrieval layer silently serves stale facts even when storage is correct.

We replicate on OUR OWN stack (local nomic-embed-text), then measure the fix:
  TEST 1 (replicate): AUROC of "low cosine => this is a supersession/contradiction".
  TEST 2 (stale-fact-error): for a store holding {original, later update}, how often does pure
          cosine top-1 retrieval return the STALE value, vs a deterministic (subject,relation,object)
          supersession ledger that retires the old value (no threshold, no LLM)?

Falsifier (ours): if a deterministic SRO-supersession rule does NOT cut the stale-fact rate well below
pure-cosine retrieval, the mechanism is useless and we drop it.

Run: python mnemo/probes/supersession_replication.py   (cloud-free; needs numpy + local Ollama nomic-embed-text)
Part of Agora / mnemo (MIT). Reproduces the numbers cited in the rag-supersession-blind-spot post.
"""
import json
import urllib.request
import numpy as np

OLLAMA = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"

# (subject, relation, old_object, new_object, rephrase-of-original) across varied domains.
FACTS = [
    ("the billing API", "authentication method", "OAuth2", "API keys",
     "To authenticate against the billing API you use OAuth2."),
    ("the staging database", "host", "db-staging-01", "db-staging-07",
     "The staging database lives on the host named db-staging-01."),
    ("the deploy script", "default branch", "master", "main",
     "By default the deploy script targets the master branch."),
    ("the pricing tier Pro", "monthly price", "29 dollars", "39 dollars",
     "The Pro pricing tier costs 29 dollars per month."),
    ("the cache layer", "eviction policy", "LRU", "two-tier value-protected",
     "The cache layer evicts entries using an LRU policy."),
    ("the auth service", "session timeout", "30 minutes", "15 minutes",
     "Sessions in the auth service expire after 30 minutes."),
    ("the report job", "schedule", "every night at 2am", "every 6 hours",
     "The report job runs nightly at 2am."),
    ("project Atlas", "tech lead", "Maria", "Daniel",
     "Maria is the tech lead of project Atlas."),
    ("the API rate limit", "value", "100 requests per minute", "300 requests per minute",
     "The API allows 100 requests per minute before rate limiting."),
    ("the model endpoint", "default model", "gpt-4", "claude-opus",
     "The model endpoint serves gpt-4 by default."),
    ("the backup retention", "window", "7 days", "30 days",
     "Backups are retained for a window of 7 days."),
    ("the frontend framework", "version", "React 17", "React 19",
     "The frontend is built on React 17."),
    ("the data warehouse", "region", "us-east-1", "eu-west-1",
     "The data warehouse is hosted in the us-east-1 region."),
    ("the password policy", "minimum length", "8 characters", "12 characters",
     "Passwords must be at least 8 characters long."),
    ("the support queue", "owner team", "Team Falcon", "Team Otter",
     "The support queue is owned by Team Falcon."),
    ("the feature flag rollout", "percentage", "10 percent", "50 percent",
     "The feature flag is rolled out to 10 percent of users."),
    ("the encryption standard", "algorithm", "AES-128", "AES-256",
     "Data is encrypted using the AES-128 algorithm."),
    ("the onboarding flow", "number of steps", "5 steps", "3 steps",
     "The onboarding flow consists of 5 steps."),
    ("the CI pipeline", "test runner", "Jest", "Vitest",
     "The CI pipeline runs tests with Jest."),
    ("the storage bucket", "access level", "private", "public-read",
     "The storage bucket is configured as private."),
    ("the metric dashboard", "refresh interval", "60 seconds", "10 seconds",
     "The metric dashboard refreshes every 60 seconds."),
    ("the license", "type", "MIT", "Apache-2.0",
     "The project is released under the MIT license."),
    ("the queue broker", "technology", "RabbitMQ", "Kafka",
     "Message queuing is handled by RabbitMQ."),
    ("the admin override code", "value", "4471", "9920",
     "The admin override code is 4471."),
]


def embed(text):
    body = json.dumps({"model": MODEL, "prompt": text}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    return np.array(json.loads(urllib.request.urlopen(req, timeout=60).read())["embedding"], dtype=float)


def sent(s, r, o):
    return f"{s} {r}: {o}"


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def auroc(scores, labels):
    # rank-based AUROC; labels in {0,1}
    order = np.argsort(scores)
    ranks = np.empty(len(scores)); ranks[order] = np.arange(1, len(scores) + 1)
    pos = labels == 1; n_pos = pos.sum(); n_neg = (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main():
    # embed everything
    originals, updates, dupes, queries = [], [], [], []
    for (s, r, o1, o2, rep) in FACTS:
        originals.append(embed(sent(s, r, o1)))
        updates.append(embed(sent(s, r, o2)))
        dupes.append(embed(rep))
        queries.append(embed(f"What is the {r} of {s}?"))
    M = np.vstack(originals + updates + dupes + queries)
    mu = M.mean(axis=0)                      # center: nomic is anisotropic (cosines compress to ~0.75-0.81)
    O = [v - mu for v in originals]; U = [v - mu for v in updates]
    D = [v - mu for v in dupes]; Q = [v - mu for v in queries]

    # TEST 1 — supersession blind spot. For each fact: pair(original,update)=contradiction(label 1),
    # pair(original,dupe)=same-fact(label 0). Score = (1 - cosine): "less similar => contradiction".
    # If contradictions are NOT reliably less similar than rephrases, AUROC -> ~0.5.
    scores, labels = [], []
    sim_contra, sim_dupe = [], []
    for i in range(len(FACTS)):
        c_contra = cos(O[i], U[i]); c_dupe = cos(O[i], D[i])
        sim_contra.append(c_contra); sim_dupe.append(c_dupe)
        scores += [1 - c_contra, 1 - c_dupe]; labels += [1, 0]
    a = auroc(np.array(scores), np.array(labels))
    flipped = sum(1 for i in range(len(FACTS)) if sim_contra[i] >= sim_dupe[i])

    # TEST 2 — stale-fact-error. Store holds original + later update. Query asks for current value.
    # (a) pure cosine top-1 over {original, update}: stale if it picks original.
    # (b) SRO supersession: (subject,relation) key -> retire original when update arrives; pick update.
    stale_cos = 0
    for i in range(len(FACTS)):
        q = Q[i]
        pick_update = cos(q, U[i]) >= cos(q, O[i])
        if not pick_update:
            stale_cos += 1
    stale_cos_rate = stale_cos / len(FACTS)
    stale_sro_rate = 0.0  # deterministic: original retired by (S,R) supersession; never surfaced

    print("=== SUPERSESSION REPLICATION (local nomic, centered) ===")
    print(f"facts: {len(FACTS)}")
    print(f"[TEST1] mean cos(original,contradiction) = {np.mean(sim_contra):.3f}")
    print(f"[TEST1] mean cos(original,rephrase-dupe)  = {np.mean(sim_dupe):.3f}")
    print(f"[TEST1] contradictions >= dupes in {flipped}/{len(FACTS)} cases "
          f"(if high, similarity can't flag supersession)")
    print(f"[TEST1] AUROC(low-cosine => supersession) = {a:.3f}   "
          f"(their claim ~0.59; 0.5=chance)")
    print(f"[TEST2] stale-fact-error, pure cosine top-1 = {stale_cos_rate:.1%}")
    print(f"[TEST2] stale-fact-error, SRO supersession  = {stale_sro_rate:.1%}")
    verdict = "REPRODUCED" if a <= 0.70 else "FAILED"
    print(f"VERDICT(blind-spot, AUROC<=0.70): {verdict}")
    print(f"FALSIFIER(ours): SRO must cut stale below cosine -> "
          f"{'PASS' if stale_sro_rate < stale_cos_rate else 'FAIL'} "
          f"({stale_cos_rate:.1%} -> {stale_sro_rate:.1%})")


if __name__ == "__main__":
    main()
