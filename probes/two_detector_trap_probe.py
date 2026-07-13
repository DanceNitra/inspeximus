"""
two_detector_trap_probe.py  --  the frontier-hunt receipt: h->0 temporal evasion is NOT free. MIT.

Context (DeepSeek-V3 #1462 layer-2 frontier). The fully-escaping poison is a sub-threshold (per-source
bias h->0), Sybil-split, reversible campaign that aggregates to a target bias B and evades the TEMPORAL
change-detector (CUSUM/QCD: detection delay ~ 2*sigma^2/h^2 * log(1/alpha) -> inf as h->0). The frontier
map treated that h->0 escape as FREE. It is not.

THE CONSERVATION: to deliver a fixed aggregate B with each source sub-threshold, the attacker needs
N ~ B/h correlated, same-direction, low-provenance sources. So driving h->0 (to blind the temporal
detector) drives N->inf, which lights up an ORTHOGONAL cross-sectional / population detector (shilling &
Sybil detection: correlated low-provenance profiles are cross-sectionally separable -- Mobasher-Burke TOIT
2007; Mehta-Nejdl PCA UMUAI 2009; SybilRank NSDI 2012). The attacker trades a per-source MAGNITUDE signal
(temporal, ~h/sigma) for a POPULATION COUNT/COHERENCE signal (~N). This probe measures both across an h
sweep at fixed B and reports the MAXIMUM B an attacker can push while BOTH detectors stay blind (B_max).

HONEST SCOPE (this BOUNDS extractable-while-invisible harm; it does NOT close the attack):
 - It does NOT zero the harm: for small enough B there IS a joint-blind window; B_max is finite, not 0,
   and scales with the detectors' sensitivity and the honest population size (a knob, not a theorem).
 - The cross-sectional gain is EMPIRICAL / bounded, NOT an information-theoretic guarantee (obfuscation &
   forged honest-graph links evade it -- Viswanath SIGCOMM 2010). Do not claim info-theoretic separation.
 - At the limit it collapses onto the Cheng-Friedman / Douceur earned-standing (Sybil) floor: an attacker
   who EARNS standing on decorrelated identities is not low-provenance, so the low-provenance-keyed
   cross-sectional detector stops watching -- E1, the bedrock residual (see the module footer).

Run: python two_detector_trap_probe.py   (zero-dependency)
"""
import math


def sweep(B, sigma=1.0, n_pool=100, alpha=0.01,
          temporal_snr_thresh=0.5, crosssec_z_thresh=3.0):
    """For a fixed target bias B, sweep per-source h; report where each detector is blind."""
    rows, joint_blind_hs = [], []
    hs = [2.0, 1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01]
    for h in hs:
        N = math.ceil(B / h)                                   # correlated sources needed for bias B
        temporal_snr = h / sigma                               # per-source magnitude signal (CUSUM)
        cusum_delay = 2 * sigma ** 2 / h ** 2 * math.log(1 / alpha)   # factor-2-correct Lorden delay
        z_conc = N / math.sqrt(n_pool)                         # excess same-direction low-prov sources vs null
        temporal_blind = temporal_snr < temporal_snr_thresh
        crosssec_blind = z_conc < crosssec_z_thresh
        joint_blind = temporal_blind and crosssec_blind
        if joint_blind:
            joint_blind_hs.append(h)
        rows.append((h, N, temporal_snr, cusum_delay, z_conc, temporal_blind, crosssec_blind, joint_blind))
    return rows, joint_blind_hs


def max_invisible_B(sigma=1.0, n_pool=100, temporal_snr_thresh=0.5, crosssec_z_thresh=3.0):
    """Largest aggregate bias an attacker can push while BOTH detectors stay blind.
    temporal-blind => h < temporal_snr_thresh*sigma ; crosssec-blind => N = B/h < crosssec_z_thresh*sqrt(n_pool).
    So B < h * crosssec_z_thresh*sqrt(n_pool), maximized as h -> temporal_snr_thresh*sigma."""
    h_max = temporal_snr_thresh * sigma
    return h_max * crosssec_z_thresh * math.sqrt(n_pool)


def main():
    sigma, n_pool = 1.0, 100
    print("Two-detector trap: temporal (per-source h/sigma) vs cross-sectional (N/sqrt(pool)) at fixed B.\n")
    for B in (3.0, 10.0, 30.0):
        rows, jb = sweep(B, sigma=sigma, n_pool=n_pool)
        print(f"=== target bias B={B} (pool={n_pool}, sigma={sigma}) ===")
        print("  h     N     tSNR   CUSUMdelay     xZ    tBlind xBlind JOINT-BLIND")
        for (h, N, t, d, z, tb, xb, jb1) in rows:
            print(f"  {h:<5} {N:<5} {t:<5.2f}  {d:>10.1f}   {z:<5.2f}  {str(tb):<5} {str(xb):<5}  {'<== EVADES BOTH' if jb1 else ''}")
        print(f"  -> joint-blind h's for B={B}: {jb or 'NONE (no h evades both)'}\n")

    Bmax = max_invisible_B(sigma=sigma, n_pool=n_pool)
    print(f"MEASURED BOUND: max aggregate bias pushable while BOTH detectors stay blind, B_max ~= {Bmax:.1f}")
    print("  (= temporal_thresh*sigma * crosssec_thresh*sqrt(pool)). Any target needing B > B_max lights up")
    print("  at least one detector. So h->0 is NOT a free escape: the temporal-invisible attacker is bounded")
    print("  to B <= B_max, and larger (higher-consequence) B forces detection. HONEST: B_max is finite, not 0,")
    print("  and is a knob (scales with detector sensitivity + honest pool sqrt) -- this BOUNDS, does not close.")
    print()
    print("BEDROCK (E1, not closed by any detector): an attacker who EARNS genuine standing on decorrelated")
    print("  identities is not low-provenance, so the cross-sectional detector stops watching. A coordinated")
    print("  earned coalition below the Byzantine threshold cannot be separated from GENUINE CONSENSUS using")
    print("  INTERNAL signals alone (Lamport-Shostak-Pease 1982; FLP 1985; Cheng-Friedman 2005: sybilproofness")
    print("  needs exogenous asymmetry). THE ONE DESIGN LEVER (textbook common-cause / Reichenbach): do NOT let")
    print("  EVIDENCE-FREE consensus drive an irreversible high-consequence decision -- require an INDEPENDENT")
    print("  external correlate/falsifier first. Then on a never-observable target (no correlate) consensus")
    print("  carries no warrant and is weighted ~0 (extractable harm -> 0); on an observable target the attacker")
    print("  must forge N INDEPENDENT causal provenances (super-linear), not N reputations. Raises the floor;")
    print("  does not remove it (correlate-verification itself recurses). External ground truth is NECESSARY.")


if __name__ == "__main__":
    main()
