"""One pinned answerer for every arm, with the call counter the cost axis needs.

Design notes that are not incidental:

* **`num_ctx` is deliberately never sent to Ollama.** Setting it forces the daemon to spin a *new* model
  instance at that context size and evict the resident one — on a box shared with other agents that is a
  side effect this harness is not allowed to have. The harness reads the resident instance's context
  length from `/api/ps` and reports it as part of the operating point instead.
* **Unique prompts only.** A 0.0 s reply from a local daemon is a cache hit, not a call. `probe()` sends a
  nonce and checks the ANSWER, never merely that a string came back.
* **Every call is counted**, because "LLM calls per write" is one of the published columns and an
  uncounted retry would understate it.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import threading
import time
import urllib.error
import urllib.request

_COUNT_LOCK = threading.Lock()


class LLMUnavailable(RuntimeError):
    """Raised with the endpoint's own words. A NOT-MEASURED row must quote the reason, not paraphrase it."""


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str = "", seed: int = 20260801,
                 temperature: float = 0.0, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.seed = seed
        self.temperature = temperature
        self.timeout = timeout
        self.calls = 0
        self.errors = 0
        self.seconds = 0.0
        self.native_ollama = self.base_url.endswith("/api") or ":11434" in self.base_url and "/v1" not in self.base_url

    # ---------------------------------------------------------------- transport
    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers)
        return json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())

    def chat(self, system: str, user: str, max_tokens: int = 64, retries: int = 3) -> str:
        """Returns the assistant text, or a string starting with `__ERR__` — never raises, so one bad
        question cannot void a whole arm. Errors are counted and surface in the result JSON."""
        for attempt in range(retries):
            t0 = time.time()
            try:
                if self.native_ollama:
                    payload = {"model": self.model, "stream": False,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}],
                               "options": {"temperature": self.temperature, "seed": self.seed,
                                           "num_predict": max_tokens}}
                    d = self._post("/chat", payload)
                    out = (d.get("message") or {}).get("content") or ""
                else:
                    payload = {"model": self.model, "temperature": self.temperature, "seed": self.seed,
                               "max_tokens": max_tokens,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}]}
                    d = self._post("/chat/completions", payload)
                    out = d["choices"][0]["message"]["content"] or ""
                with _COUNT_LOCK:
                    self.calls += 1
                    self.seconds += time.time() - t0
                return out
            except Exception as e:                                  # noqa: BLE001
                with _COUNT_LOCK:
                    self.seconds += time.time() - t0
                if attempt == retries - 1:
                    with _COUNT_LOCK:
                        self.errors += 1
                    detail = ""
                    if isinstance(e, urllib.error.HTTPError):
                        try:
                            detail = e.read().decode("utf-8", "replace")[:200]
                        except Exception:                           # noqa: BLE001
                            detail = ""
                    return f"__ERR__{type(e).__name__}: {e} {detail}"
                time.sleep(2 * (attempt + 1))
        return "__ERR__unreachable"

    # ---------------------------------------------------------------- liveness
    def probe(self) -> dict:
        """Liveness with a NONCE and an answer check. A tier that returns a cached string is not alive."""
        a, b = random.Random(time.time_ns()).randrange(100, 900), random.Random(os.getpid()).randrange(10, 90)
        t0 = time.time()
        out = self.chat("Reply with only the digits, nothing else.",
                        f"What is {a} plus {b}?", max_tokens=12, retries=1)
        dt = time.time() - t0
        ok = str(a + b) in (out or "")
        return {"alive": bool(ok), "latency_s": round(dt, 3), "answer": (out or "")[:80],
                "expected": a + b, "model": self.model, "endpoint": self.base_url}

    def require(self) -> dict:
        p = self.probe()
        if not p["alive"]:
            raise LLMUnavailable(f"{self.model} @ {self.base_url}: {p['answer'][:200]!r}")
        return p

    def operating_point(self) -> dict:
        op = {"model": self.model, "endpoint": self.base_url, "temperature": self.temperature,
              "seed": self.seed, "calls": self.calls, "errors": self.errors,
              "llm_seconds": round(self.seconds, 1)}
        op["resident_context_length"] = _resident_ctx(self.base_url, self.model)
        return op


def _resident_ctx(base_url: str, model: str):
    """The context length of the ALREADY-RESIDENT instance. Part of the operating point: an arm that
    silently truncated at 2048 and an arm that saw 32768 are not the same measurement."""
    if ":11434" not in base_url:
        return None
    try:
        host = base_url.split("/v1")[0].split("/api")[0]
        d = json.loads(urllib.request.urlopen(host + "/api/ps", timeout=10).read())
        for m in d.get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m.get("context_length")
    except Exception:                                               # noqa: BLE001
        return None
    return None


def load_env(path: str | None = None) -> dict:
    """Read an agora-style .env. Secrets never appear on a command line; they are read from the file."""
    p = pathlib.Path(path or os.environ.get("AGORA_ENV_FILE", r"C:/Users/Danculus/agora/server/.env"))
    env: dict[str, str] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def default_answerer() -> LLM:
    """The pinned answerer. Explicit env wins; otherwise the local Ollama daemon, whose model is pinned to
    whatever `PARITY_MODEL` says (default qwen2.5:7b — chosen because it is already resident, so using it
    loads nothing and evicts nothing)."""
    env = load_env()
    base = os.environ.get("PARITY_LLM_BASE_URL") or "http://127.0.0.1:11434/api"
    model = os.environ.get("PARITY_MODEL") or env.get("AGORA_LLM_MODEL_CHEAP") or "qwen2.5:7b"
    key = os.environ.get("PARITY_LLM_API_KEY") or ""
    return LLM(base, model, key)
