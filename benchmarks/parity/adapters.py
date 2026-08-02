"""One interface, several memory systems. Adding a system is one class.

Every arm implements the same six operations, and every axis is scored by the same reader, so the only
thing that differs between two rows of the published table is the memory layer.

    open(ns)                  -> a fresh, isolated namespace (own store dir / user_id / group_id / agent)
    write(text, key, object)  -> the arm's ordinary write path
    route(text, key, object)  -> the arm's BEST exposed path for a revert utterance; defaults to write()
    read(query, k)            -> the arm's native read surface, as an ORDERED list of strings
    erase(value)              -> the arm's BEST exposed deletion path for a literal value
    data_dir()                -> where it persists, so residue can be checked in the bytes

Two fairness rules are load-bearing and are enforced by construction, not by good intentions:

1. **Score the best path a system exposes, never a primitive it does not have.** `route()` falling back to
   `write()` is not a trick — writing the utterance as another fact IS what a system without a revert
   channel does, and it is what a competent engineer using that system would get.
2. **A competitor scoring 0.000 is OUR bug until a positive control says otherwise.** Every competitor arm
   ships a `positive_control()` that must pass before any number from it is recorded. This rule already
   caught two of our own defects in a previous mem0 arm (a `sess[:6000]` truncation and a `limit=` kwarg
   the API ignores in favour of `top_k`), both of which had produced a flattering zero.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


class Unavailable(RuntimeError):
    """Raised with the reason a system could not be measured. The reason is published verbatim."""


# ---------------------------------------------------------------------------- base
class Arm:
    name = "arm"
    needs_llm = False
    needs_gpu = False

    def __init__(self, **cfg):
        self.cfg = cfg
        self.llm_calls = 0
        self._tmp: list[str] = []

    def info(self) -> dict:
        return {"arm": self.name, "version": self.version(), "needs_llm": self.needs_llm}

    def version(self) -> str:
        return "?"

    # -- lifecycle
    def open(self, ns: str):
        raise NotImplementedError

    def close(self):
        for d in self._tmp:
            shutil.rmtree(d, ignore_errors=True)
        self._tmp.clear()

    def _mktmp(self, prefix: str) -> str:
        d = tempfile.mkdtemp(prefix=prefix)
        self._tmp.append(d)
        return d

    # -- operations (a session is whatever open() returned)
    def write(self, s, text: str, key: str | None = None, object: str | None = None) -> None:
        raise NotImplementedError

    def route(self, s, text: str, key: str | None = None, object: str | None = None) -> dict:
        """Best exposed path for a revert utterance. Default: no revert channel -> store the utterance."""
        self.write(s, text, key=key, object=object)
        return {"path": "write (no revert channel exposed)", "steps": 1, "llm_calls": 0}

    def read(self, s, query: str, k: int = 6) -> list[str]:
        raise NotImplementedError

    def erase(self, s, value: str) -> dict:
        raise NotImplementedError

    def data_dir(self, s) -> str | None:
        return None

    # -- the mandatory gate
    def positive_control(self) -> dict:
        """Smallest input the system must handle: three writes, then read back the planted fact."""
        planted = "the positive control marker for pc is zephyr991"
        try:
            s = self.open("positivecontrol")
            self.write(s, "the alpha setting for pc is aardvark101")
            self.write(s, planted)
            self.write(s, "the omega setting for pc is walrus303")
            hits = self.read(s, "what is the positive control marker for pc?", k=10)
        except Exception as e:                                        # noqa: BLE001
            return {"arm": self.name, "passes": False,
                    "reason": f"{type(e).__name__}: {e}"[:400], "records": 0, "found": False}
        found = any("zephyr991" in (h or "").lower() for h in hits)
        return {"arm": self.name, "passes": bool(hits) and found, "records": len(hits), "found": found,
                "sample": [(h or "")[:90] for h in hits[:3]],
                "reason": "" if (hits and found) else
                          ("write path stored nothing" if not hits else
                           "planted fact not retrievable — investigate before recording any score")}


# ---------------------------------------------------------------------------- inspeximus
class InspeximusArm(Arm):
    """This repo's build. `keyed=False` is the `naive` control: the identical store and the identical
    retriever with supersession and echo_guard switched off, so any difference is attributable to the
    integrity layer rather than to two different people's ranking code."""

    def __init__(self, keyed: bool = True, embed=None, label: str | None = None, **cfg):
        super().__init__(**cfg)
        self.keyed = keyed
        self.embed = embed
        self.name = label or ("inspeximus" if keyed else "naive")

    def version(self) -> str:
        import inspeximus
        return getattr(inspeximus, "__version__", "?")

    def open(self, ns):
        from inspeximus import Inspeximus
        d = self._mktmp(f"parity_{self.name}_")
        m = Inspeximus(path=os.path.join(d, "store.json"), embed=self.embed,
                       echo_guard=bool(self.keyed))
        m._parity_dir = d
        return m

    def write(self, s, text, key=None, object=None):
        s.remember(text, key=key if self.keyed else None, object=object if self.keyed else None)

    def route(self, s, text, key=None, object=None):
        if not self.keyed:
            return super().route(s, text, key=key, object=object)
        out = s.route(text, key=key, object=object, policy="safe")
        return {"path": "route() intent router (deterministic, no LLM)", "steps": 1, "llm_calls": 0,
                "action": out.get("action") if isinstance(out, dict) else str(out)[:60]}

    def read(self, s, query, k=6):
        # reinforce=False: recall() otherwise mutates value on read, making results order-dependent.
        return [h["text"] for h in s.recall(query, k=k, reinforce=False)]

    def erase(self, s, value):
        v = value.lower()
        res = s.forget(where=lambda r: v in (r.get("text") or "").lower(),
                       request_id=f"parity-{int(time.time())}")
        s._save(force=True)
        return {"path": "forget(where=...) hard delete", "steps": 1, "llm_calls": 0,
                "deleted": res.get("forgotten", 0), "receipt": bool(getattr(s, "tombstones", None))}

    def data_dir(self, s):
        return getattr(s, "_parity_dir", None)


# ---------------------------------------------------------------------------- BM25
class Bm25Arm(Arm):
    """Okapi BM25 over a keep-everything list — the strongest zero-LLM retrieval baseline, and an
    INDEPENDENT implementation rather than our own retriever with a flag flipped. It is on the table
    mainly to be the row we expect to lose to on paraphrased retrieval."""

    name = "bm25"

    def version(self) -> str:
        try:
            import importlib.metadata as md
            return "rank_bm25 " + md.version("rank_bm25")
        except Exception:                                             # noqa: BLE001
            return "rank_bm25 ?"

    def open(self, ns):
        import importlib.util
        if importlib.util.find_spec("rank_bm25") is None:
            raise Unavailable("rank_bm25 not installed (benchmark-only dependency; the inspeximus "
                              "library itself stays zero-dependency)")
        return {"docs": [], "dir": self._mktmp("parity_bm25_")}

    @staticmethod
    def _tok(t: str):
        return [w for w in "".join(c if c.isalnum() else " " for c in (t or "").lower()).split() if w]

    def write(self, s, text, key=None, object=None):
        s["docs"].append(text)

    def read(self, s, query, k=6):
        if not s["docs"]:
            return []
        from rank_bm25 import BM25Okapi
        bm = BM25Okapi([self._tok(d) for d in s["docs"]])
        scores = bm.get_scores(self._tok(query))
        order = sorted(range(len(s["docs"])), key=lambda i: -scores[i])[:k]
        return [s["docs"][i] for i in order]

    def erase(self, s, value):
        v = value.lower()
        before = len(s["docs"])
        s["docs"] = [d for d in s["docs"] if v not in d.lower()]
        return {"path": "drop every document containing the value", "steps": 1, "llm_calls": 0,
                "deleted": before - len(s["docs"]), "receipt": False}

    def data_dir(self, s):
        return s["dir"]


# ---------------------------------------------------------------------------- mem0
class Mem0Arm(Arm):
    """mem0 in a config it is actually documented to run in. The LLM and embedder are injected rather
    than hard-coded so the same adapter serves the native OpenAI stack and a local Ollama one; whichever
    was used is recorded in the operating point of every cell."""

    name = "mem0"
    needs_llm = True

    def __init__(self, llm_provider="openai", model="gpt-4o-mini", base_url=None, api_key="",
                 embedder=("openai", "text-embedding-3-small"), **cfg):
        super().__init__(**cfg)
        self.model, self.base_url, self.api_key = model, base_url, api_key
        self.llm_provider, self.embedder = llm_provider, embedder

    def version(self) -> str:
        try:
            import importlib.metadata as md
            return "mem0ai " + md.version("mem0ai")
        except Exception:                                             # noqa: BLE001
            return "mem0ai ?"

    def _memory(self, d):
        from mem0 import Memory
        llm_cfg = {"model": self.model, "temperature": 0.0}
        if self.base_url:
            llm_cfg.update(openai_base_url=self.base_url, api_key=self.api_key or "ollama")
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
        emb_provider, emb_model = self.embedder
        emb_cfg = {"model": emb_model}
        if emb_provider == "ollama":
            emb_cfg["ollama_base_url"] = "http://localhost:11434"
        return Memory.from_config({
            "llm": {"provider": self.llm_provider, "config": llm_cfg},
            "embedder": {"provider": emb_provider, "config": emb_cfg},
            "vector_store": {"provider": "qdrant",
                             "config": {"path": os.path.join(d, "qd"), "on_disk": True}},
            "history_db_path": os.path.join(d, "history.db")})

    def open(self, ns):
        try:
            d = self._mktmp("parity_mem0_")
            return {"m": self._memory(d), "uid": ns, "dir": d}
        except Exception as e:                                        # noqa: BLE001
            raise Unavailable(f"mem0 init failed: {type(e).__name__}: {e}"[:300]) from e

    def write(self, s, text, key=None, object=None):
        s["m"].add(text, user_id=s["uid"])
        self.llm_calls += 1                       # mem0 runs extraction on every add()

    def read(self, s, query, k=6):
        # top_k, NOT limit: mem0 ignores `limit=` and silently returns its default. That exact defect
        # produced a 0.000 in an earlier run here and is the reason the positive control is mandatory.
        r = s["m"].search(query, filters={"user_id": s["uid"]}, top_k=k) or {}
        hits = r.get("results") if isinstance(r, dict) else r
        return [(h.get("memory") or h.get("text") or "") for h in (hits or [])]

    def erase(self, s, value):
        """Best exposed path: find the memories carrying the value and delete them by id."""
        r = s["m"].search(value, filters={"user_id": s["uid"]}, top_k=50) or {}
        hits = r.get("results") if isinstance(r, dict) else r
        n, steps = 0, 1
        for h in (hits or []):
            if value.lower() in (h.get("memory") or "").lower():
                try:
                    s["m"].delete(memory_id=h["id"])
                    n += 1
                    steps += 1
                except Exception:                                     # noqa: BLE001
                    pass
        self.llm_calls += 1
        return {"path": "search(value) then delete(memory_id) per hit", "steps": steps,
                "llm_calls": 1, "deleted": n, "receipt": False}

    def data_dir(self, s):
        return s["dir"]


# ---------------------------------------------------------------------------- Graphiti
class GraphitiArm(Arm):
    """Graphiti against a live neo4j, its own entity/edge extraction pipeline, one group_id per thread."""

    name = "graphiti"
    needs_llm = True

    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="testpassword123", **cfg):
        super().__init__(**cfg)
        self.uri, self.user, self.password = uri, user, password
        self._g = None

    def version(self) -> str:
        try:
            import importlib.metadata as md
            return "graphiti-core " + md.version("graphiti-core")
        except Exception:                                             # noqa: BLE001
            return "graphiti-core ?"

    def _client(self):
        import asyncio
        from graphiti_core import Graphiti
        if self._g is None:
            self._g = Graphiti(self.uri, self.user, self.password)
            asyncio.get_event_loop().run_until_complete(self._g.build_indices_and_constraints())
        return self._g

    def open(self, ns):
        try:
            self._client()
        except Exception as e:                                        # noqa: BLE001
            raise Unavailable(f"graphiti/neo4j unavailable: {type(e).__name__}: {e}"[:300]) from e
        return {"gid": f"parity_{ns}_{int(time.time()*1000)}", "n": 0}

    def write(self, s, text, key=None, object=None):
        import asyncio
        import datetime
        from graphiti_core.nodes import EpisodeType
        g = self._client()
        t0 = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
        asyncio.get_event_loop().run_until_complete(g.add_episode(
            name=f"m{s['n']}", episode_body=text, source_description="chat",
            reference_time=t0 + datetime.timedelta(minutes=s["n"]),
            source=EpisodeType.message, group_id=s["gid"]))
        s["n"] += 1
        self.llm_calls += 1

    def read(self, s, query, k=6):
        import asyncio
        g = self._client()
        res = asyncio.get_event_loop().run_until_complete(
            g.search(query, group_ids=[s["gid"]], num_results=k))
        return [getattr(x, "fact", str(x)) for x in (res or [])]

    def erase(self, s, value):
        raise Unavailable("no per-value deletion path was exercised for graphiti in this run")


# ---------------------------------------------------------------------------- registry
def build_arm(name: str, **cfg) -> Arm:
    if name == "inspeximus":
        return InspeximusArm(keyed=True, **cfg)
    if name == "naive":
        return InspeximusArm(keyed=False, **cfg)
    if name == "bm25":
        return Bm25Arm(**cfg)
    if name == "mem0":
        return Mem0Arm(**cfg)
    if name == "graphiti":
        return GraphitiArm(**cfg)
    raise ValueError(f"unknown arm {name!r}")


LOCAL_ARMS = ("inspeximus", "naive", "bm25")
COMPETITOR_ARMS = ("mem0", "graphiti")
