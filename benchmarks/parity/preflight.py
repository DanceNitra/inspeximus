"""Hard pre-flight gate for anything in this harness that calls a model.

The box runs ONE RTX 3090 shared with other long-lived processes, and this harness is forbidden to stop
any of them — a coordinator owns that decision and other agents run concurrently. So the gate REFUSES to
start rather than producing a contended number that later gets quoted as a system's latency.

It is a gate, not a warning: `require_gpu()` raises unless the box is quiet, and the only way past it is
an explicit `--allow-contended-gpu`, which stamps `gpu_contended: true` onto every cell it produces.

Why it also checks for `llama-server.exe`: a previous measurement here matched on `ollama*` and read the
box as idle while three `llama-server.exe` processes held 20 GB. A check that cannot see its target
reports SAFE.
"""
from __future__ import annotations

import shutil
import subprocess

MIN_FREE_VRAM_MB = 20 * 1024
BLOCKING_PROCESSES = ("llama-server.exe", "llama-server")


def gpu_state() -> dict:
    """Measured VRAM + the resident model-server processes. Never raises: an unreadable GPU is reported
    as unknown, and unknown does NOT pass the gate."""
    out: dict = {"vram_total_mb": None, "vram_used_mb": None, "vram_free_mb": None,
                 "blocking_processes": [], "nvidia_smi": bool(shutil.which("nvidia-smi"))}
    if out["nvidia_smi"]:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                t, u, f = (int(x.strip()) for x in r.stdout.strip().splitlines()[0].split(","))
                out.update(vram_total_mb=t, vram_used_mb=u, vram_free_mb=f)
        except Exception as e:                                     # noqa: BLE001 - reported, not raised
            out["nvidia_smi_error"] = f"{type(e).__name__}: {e}"
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                out["compute_apps"] = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
        except Exception:                                          # noqa: BLE001
            pass
    for name in BLOCKING_PROCESSES:
        n = _count_process(name)
        if n:
            out["blocking_processes"].append({"name": name, "count": n})
    return out


def _count_process(name: str) -> int:
    """Count processes by image name. Uses tasklist on Windows, pgrep elsewhere; a failure to run the
    tool counts as UNKNOWN (-1), which the gate treats as blocking rather than as zero."""
    try:
        if shutil.which("tasklist"):
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return -1
            return sum(1 for l in r.stdout.splitlines() if name.lower() in l.lower())
        if shutil.which("pgrep"):
            r = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True, timeout=30)
            return len([l for l in r.stdout.splitlines() if l.strip()]) if r.returncode in (0, 1) else -1
    except Exception:                                              # noqa: BLE001
        return -1
    return 0


def check(min_free_mb: int = MIN_FREE_VRAM_MB) -> tuple[bool, str, dict]:
    """(ok, reason, state). `ok` is False whenever the box is contended OR unreadable."""
    st = gpu_state()
    if st["blocking_processes"]:
        return False, "model-server processes resident: " + ", ".join(
            f"{b['name']}x{b['count']}" for b in st["blocking_processes"]), st
    free = st.get("vram_free_mb")
    if free is None:
        return False, "free VRAM could not be measured (no readable nvidia-smi) — unknown is not idle", st
    if free < min_free_mb:
        return False, f"free VRAM {free} MiB < required {min_free_mb} MiB", st
    return True, f"free VRAM {free} MiB, no blocking model servers", st


def require_gpu(allow_contended: bool = False, min_free_mb: int = MIN_FREE_VRAM_MB) -> dict:
    """Raise unless the GPU is quiet. Returns the state dict, stamped with `gpu_contended`."""
    ok, reason, st = check(min_free_mb)
    st["gpu_contended"] = not ok
    st["preflight_reason"] = reason
    if ok:
        return st
    if allow_contended:
        print(f"  [preflight] OVERRIDDEN — {reason}. Every cell stamped gpu_contended=true; "
              f"latencies are UPPER BOUNDS, not this system's number.", flush=True)
        return st
    raise SystemExit(
        "GPU PRE-FLIGHT REFUSED: " + reason + "\n"
        "  This harness never stops another process — a coordinator owns that decision and other\n"
        "  agents run concurrently. Quiesce the GPU and re-run, or pass --allow-contended-gpu to\n"
        "  measure anyway (every cell will be stamped gpu_contended=true and must not be quoted\n"
        "  as a clean latency).")


if __name__ == "__main__":
    ok, reason, st = check()
    print(("PASS: " if ok else "REFUSE: ") + reason)
    for k, v in st.items():
        print(f"  {k} = {v}")
    raise SystemExit(0 if ok else 1)
