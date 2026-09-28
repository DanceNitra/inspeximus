"""A heartbeat for long probes: the phase and the elapsed time on stderr, at least every 30 s.

A long run must print progress (2026-09-28): on the Windows CI trial, identity_gate_supersession_probe
and what_a_turn_pays_... ran silent for up to 529 s before their first line, so a timeout could not say
whether they were working or hung, or where they stopped. stderr, so a probe's stdout and receipt stay
byte-identical; and only when the probe runs as a script, so a test that imports it starts no thread.
"""
import os
import sys
import threading
import time

_state = {"phase": "starting", "t0": None, "name": None}


def phase(name):
    """Name the stage the probe is in now; the next heartbeat reports it."""
    _state["phase"] = str(name)


def start(probe_file, every=30.0):
    """Start the heartbeat for `probe_file` (pass __file__). A no-op unless that file is the running script."""
    main = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main or os.path.abspath(main) != os.path.abspath(probe_file) or _state["t0"] is not None:
        return False
    _state["t0"], _state["name"] = time.time(), os.path.basename(probe_file)

    def beat():
        while True:
            time.sleep(every)
            print("[heartbeat] %s phase=%s elapsed=%.0fs" % (_state["name"], _state["phase"],
                                                             time.time() - _state["t0"]),
                  file=sys.stderr, flush=True)
    threading.Thread(target=beat, name="probe-heartbeat", daemon=True).start()
    return True
