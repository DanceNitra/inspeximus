"""A long probe prints its phase and elapsed time at least every 30 s (probes/_heartbeat.py, 2026-09-28).

On the Windows CI trial two probes ran silent for up to 529 s, so a timeout could not tell working from hung.
The heartbeat goes to stderr, so stdout and receipts are unchanged, and it starts only when the probe is
the running script. A control shows the same helper stays silent when a test merely imports it.
"""
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = os.path.join(ROOT, "probes")
SLOW = ["infer_lineage_precision.py", "reopen_interval_readpath.py", "database_is_locked_under_many_readers.py",
        "dogfood_cross_session.py", "recall_iterative_surface_multihop.py", "identity_gate_supersession_probe.py",
        "what_a_turn_pays_for_recall_before_and_after_the_queue.py"]


def test_a_script_gets_its_phase_and_elapsed_time_on_stderr(tmp_path):
    script = tmp_path / "slow_probe.py"
    script.write_text(textwrap.dedent("""
        import sys, time
        sys.path.insert(0, %r)
        import _heartbeat
        assert _heartbeat.start(__file__, every=0.2)
        _heartbeat.phase("seed 1 of 2")
        time.sleep(0.7)
        print("result line")
    """ % PROBES), encoding="utf-8")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "result line", r.stdout                  # stdout is the probe's own
    beats = [ln for ln in r.stderr.splitlines() if ln.startswith("[heartbeat] slow_probe.py phase=seed 1 of 2")]
    assert len(beats) >= 2 and "elapsed=" in beats[0], r.stderr


def test_an_import_starts_no_heartbeat():
    sys.path.insert(0, PROBES)
    try:
        import _heartbeat
        assert _heartbeat.start(os.path.join(PROBES, "identity_gate_supersession_probe.py")) is False  # control
    finally:
        sys.path.remove(PROBES)


def test_the_slow_probes_start_it():
    for name in SLOW:
        src = open(os.path.join(PROBES, name), encoding="utf-8").read()
        assert "_heartbeat.start(__file__)" in src, name
