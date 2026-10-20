"""Every worker of tools/mutation_check_parallel.py takes its next entry from one queue (owner, 2026-10-20).

The entries used to be dealt out before the run, three to each of 16 workers. In the 3.21.1 mutload pass 2 the workers
that drew fast entries finished in a minute and then stood idle, while 5 workers that drew the slow timing tests ran
for 4 more minutes: "vidim ze nic nejde parallelne". Each case here replaces the worker with one that sleeps per
entry, so the split depends only on the queue."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import mutation_check_parallel as mcp  # noqa: E402


def _run(monkeypatch, tmp_path, names, workers, sleep, durations=None):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{"name": n, "file": "inspeximus/core.py", "old": "x", "new": "y", "tests": []}
                                for n in names]), encoding="utf-8")
    root = tmp_path / "root"
    (root / ".mutwt").mkdir(parents=True)
    if durations:
        (root / ".mutwt" / "durations.json").write_text(json.dumps(durations), encoding="utf-8")
    took = {}

    def fake_worker(idx, mutations, base, keep):
        got = took.setdefault(idx, [])
        for m in mutations:
            got.append(m["name"])
            time.sleep(sleep(m["name"]))
        return {"idx": idx, "rc": 0, "secs": 0.0, "log": None, "totals": (len(got), len(got), 0, 0),
                "survived": [], "skipped": [], "preflight_red": [], "expected": list(got), "killed_clipped": [],
                "stderr_tail": "", "stdout": "", "durations": {}}
    monkeypatch.setattr(mcp, "ROOT", str(root))
    monkeypatch.setattr(mcp, "_run_worker", fake_worker)
    monkeypatch.setattr(mcp, "_dirty_tracked", lambda: [])
    monkeypatch.setattr(sys, "argv", ["mutation_check_parallel.py", str(spec), "--workers", str(workers)])
    rc = mcp.main()
    return rc, took


def test_a_worker_that_finishes_takes_the_next_entry(monkeypatch, tmp_path):
    names = ["m%d" % i for i in range(10)]
    rc, took = _run(monkeypatch, tmp_path, names, 2, lambda n: 0.6 if n == "m0" else 0.02)
    assert rc == 0
    # The worker that drew the slow entry ran it alone; the other worker ran the other nine meanwhile. Dealt out
    # in advance, each worker would have had five.
    assert sorted(took.values(), key=len) == [["m0"], names[1:]], took


def test_the_slowest_entries_of_the_last_run_go_first(monkeypatch, tmp_path):
    names = ["m%d" % i for i in range(6)]
    rc, took = _run(monkeypatch, tmp_path, names, 1, lambda n: 0.0,
                    durations={n: float(i) for i, n in enumerate(names)})
    assert rc == 0 and took[0] == list(reversed(names)), took


def test_an_entry_with_no_recorded_time_goes_first(monkeypatch, tmp_path):
    """CONTROL: an entry the last run never timed might be the slow one, so it does not wait at the end."""
    names = ["m0", "m1", "new"]
    rc, took = _run(monkeypatch, tmp_path, names, 1, lambda n: 0.0, durations={"m0": 1.0, "m1": 2.0})
    assert rc == 0 and took[0] == ["new", "m1", "m0"], took


def test_every_entry_runs_exactly_once(monkeypatch, tmp_path):
    names = ["m%d" % i for i in range(40)]
    rc, took = _run(monkeypatch, tmp_path, names, 8, lambda n: 0.001 * (hash(n) % 7))
    assert rc == 0 and sorted(n for got in took.values() for n in got) == sorted(names)
