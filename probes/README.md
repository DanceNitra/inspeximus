# mnemo probes — runnable memory-reliability tests

Small, single-file, cloud-free probes for the two failure modes that hit persistent agent memory hardest:
**what to keep when the store is finite** and **how to keep a false memory from hardening into a durable
trait**. They are the reference implementations behind the mechanisms shipped in [`mnemo`](../).

| probe | runs | shows |
|---|---|---|
| [`eviction_twotier.py`](eviction_twotier.py) | `python eviction_twotier.py` (numpy only) | No single eviction rule wins all workloads; a **two-tier store** (value-protected + recency-aged) matches/beats the best single rule in every regime. |
| [`corroboration_poison.py`](corroboration_poison.py) | `python corroboration_poison.py` (uses `../mnemo.py`) | A recall-pumped, self-asserted false memory **must not** graduate to durable memory; corroboration-gating (earned outcome or distinct, entity-resolved sources) blocks it while legit memories still graduate. |

Both print a verdict line. MIT-licensed — link them or vendor them, whatever suits.

## Memory governance as a scarcity multiplier

The two probes are two faces of one idea. When a memory store is **finite**, the governance policy — *what
gets promoted, protected, and evicted* — is a **multiplier** on the value of every byte you keep:

- **Eviction (what survives).** Under a flood of unique junk, a recency rule keeps the junk and evicts the
  rare-but-critical items: the same capacity yields near-zero useful recall. A value-protected tier flips
  that — the same bytes now hold the items that matter. Governance turned identical capacity from worthless
  to valuable.
- **Promotion (what becomes durable).** If promotion is gated on recall frequency, an attacker (or just a
  feedback loop) pumps a false statement into permanence — the store's capacity is spent storing a lie. Gate
  on **corroboration** instead, and the same capacity stores trustworthy facts.

So scarcity doesn't just bound *how much* you remember; the governance policy decides whether finite memory is
an asset or a liability. The sybil-resistant piece matters here: corroboration must count **distinct,
attributable** sources (entity-resolved), or the multiplier runs backwards — an attacker mints "independent"
confirmations and the gate amplifies poison instead of blocking it. Verified identity binding is what gives
the gate attributable provenance.
