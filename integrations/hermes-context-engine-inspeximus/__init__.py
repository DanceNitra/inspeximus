"""Hermes context engine that digests a large tool result instead of discarding it.

Hermes already prunes old tool results: its summarizers replace the payload with one line, and
for a tool it has no summarizer for, that line is a byte count. The content is gone, so the
agent re-runs the tool to get it back. This engine keeps Hermes's conversation compaction and
replaces only that step: an oversized result becomes an intent-ranked digest plus the path of
the file holding the whole output, which is one read away.

It works on the request, through select_context, which Hermes runs before every provider call
and which never touches the stored transcript. prune_tool_results_only is overridden as well,
but it is not the main path: Hermes calls it only when the context is over threshold AND
compaction is blocked, so an engine that works there alone does nothing on an ordinary turn.

Measured on the owner's own Hermes transcript, 264 tool results over 2,000 characters
(probes/hermes_prune_vs_our_saver_on_his_transcript.py in the agora repo, 2026-10-29). Of the
question's terms that appear only in the middle of the output, where neither the command echo
nor the exit status can carry them:

  Hermes's prune        0.5% of the bytes,  0.0% of those terms
  this engine          27.3% of the bytes, 61.5% of those terms
  truncation, same budget           27.4%, 54.9%

Two findings shaped the code. Hermes's pass demotes a digest as readily as raw output, which
took the figure back to 0.0%, so _demote_tool_result_at spares a digest during the proactive
pass and still yields to the pressure pass. And mixing truncation into the budget lost on the
same metric (58.8%), so the digest is not padded with a head and a tail.

Lexical ranking is the default because the embedder earned nothing here: it reordered 62 of the
264 results and left the outcome unchanged, at about half a second per result.

Install: copy this directory to <HERMES_HOME>/plugins/inspeximus/ and set "context.engine:
inspeximus" in config.yaml. Requires inspeximus>=3.26.0 in the environment Hermes runs in.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.context_compressor import ContextCompressor

logger = logging.getLogger("plugins.context_engine.inspeximus")

# Every digest starts with this line, which is how the engine recognizes its own work on a later
# pass and how a reader of the transcript knows the output was replaced rather than lost.
MARKER = "[inspeximus] digest of the full output; the file below holds all of it"

# The smallest result worth digesting. Hermes's own floor is 8,000 characters, which on the
# measured transcript left 59.7% of the tool bytes untouched; at 2,000 it leaves 22.7%.
MIN_RESULT_CHARS = 2000

# How many messages at the end of the request keep their output whole: the window the agent is
# reasoning in right now. This is deliberately NOT Hermes's protect_last_n, which is 20 messages
# and governs its LLM compaction. At 20 a short session digests nothing, and the point of the
# engine is that a large output never fills the window in the first place. A digested result is
# still recoverable from the capture file it names.
PROTECT_LAST_MESSAGES = 6


class InspeximusContextEngine(ContextCompressor):
    """Hermes's compressor with the tool-result step taken over by the inspeximus saver."""

    @property
    def name(self) -> str:
        return "inspeximus"

    def __init__(self, **kwargs: Any) -> None:
        # The host builds a plugin engine with no arguments and calls update_model() afterwards,
        # so the model is empty here on purpose. The prune runs on every turn: a digest costs no
        # model call, which is why it does not wait for the parent's token trigger.
        kwargs.setdefault("model", "")
        kwargs.setdefault("proactive_prune_tokens", 1)
        kwargs.setdefault("proactive_prune_min_result_chars", MIN_RESULT_CHARS)
        super().__init__(**kwargs)
        self._digest_cache: dict[str, Optional[str]] = {}
        self.protect_last_messages = PROTECT_LAST_MESSAGES
        self.digests = 0
        self.bytes_in = 0
        self.bytes_kept = 0

    # --- the step this engine replaces -------------------------------------------------

    def select_context(
        self, request_messages: list[dict[str, Any]], *,
        conversation_messages: list[dict[str, Any]] | None = None,
        incoming_message: dict[str, Any] | None = None, budget_tokens: int = 0,
    ) -> Optional[list[dict[str, Any]]]:
        """Digest the oversized tool results in THIS request, leaving the transcript whole.

        This is the engine's main path. Hermes calls prune_tool_results_only only when the
        context is already over threshold AND compaction is blocked, so an engine that works
        there alone does nothing on an ordinary turn. select_context runs on every provider
        request, is fail-open, and is request-only: the session database keeps the full output,
        and the capture file is a convenience for the agent rather than the only copy.

        Returns None when nothing changed, which keeps the request byte-identical and the
        prompt cache intact. A digest is cached by the output's hash, so a result digested on
        one turn yields the same bytes on the next and the cache prefix stays stable.
        """
        out = list(request_messages)
        tail_from = max(0, len(out) - self.protect_last_messages)
        calls = self._call_arguments(out)
        changed = False
        for i, msg in enumerate(out[:tail_from]):
            text = self._cached_digest(msg, calls)
            if text is not None:
                out[i] = {**msg, "content": text}
                changed = True
        return out if changed else None

    @staticmethod
    def _call_arguments(messages: list[dict[str, Any]]) -> dict[str, str]:
        """tool_call_id -> the arguments of the call that produced the result.

        The arguments are the best intent available: the probe measured 76.7% of the question's
        terms surviving when the digest ranks against them, against 56.1% when it ranks against
        the user's last message. They live on the assistant message, which is in the same list.
        """
        out: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for call in msg.get("tool_calls") or ():
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                cid = call.get("id") or call.get("call_id")
                args = fn.get("arguments")
                if isinstance(cid, str) and isinstance(args, str):
                    out[cid] = args
        return out

    def _cached_digest(self, msg: dict[str, Any], calls: dict[str, str]) -> Optional[str]:
        """The digest for this result, computed once per distinct output.

        Without the cache a result would be re-digested on every request: a new capture file
        each turn, and a digest whose bytes could differ, which breaks the prompt cache prefix
        the host rebuilds from the selected list.
        """
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, str) or len(content) < MIN_RESULT_CHARS:
            return None
        if content.startswith(MARKER):
            return None
        import hashlib

        key = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
        if key not in self._digest_cache:
            self._digest_cache[key] = self._digest_message(msg, calls)
        return self._digest_cache[key]

    def prune_tool_results_only(
        self, messages: list[dict[str, Any]], current_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Digest every oversized tool result outside the protected tail, then let Hermes's own
        deterministic passes (duplicate results, stale images) run over what is left."""
        out = list(messages)
        tail_from = max(0, len(out) - self.protect_last_messages)
        calls = self._call_arguments(out)
        digested = 0
        for i, msg in enumerate(out[:tail_from]):
            text = self._digest_message(msg, calls)
            if text is not None:
                replaced = {**msg, "content": text}
                replaced.pop("api_content", None)    # the sidecar still holds the old payload
                out[i] = replaced
                digested += 1
        messages, pruned = super().prune_tool_results_only(out, current_tokens)
        return messages, pruned + digested

    def _digest_message(self, msg: dict[str, Any], calls: Optional[dict[str, str]] = None) -> Optional[str]:
        """The replacement for one tool result, or None to leave it alone."""
        if msg.get("role") != "tool":
            return None
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < MIN_RESULT_CHARS:
            return None
        if content.startswith(MARKER):
            return None
        try:
            from inspeximus.context_saver import (
                SEPARATOR, new_capture_path, save, sweep_captures,
            )
        except Exception:                            # noqa: BLE001 -- never cost the output
            logger.debug("inspeximus is not importable; leaving the result to Hermes", exc_info=True)
            return None
        try:
            result = save(content, self._intent(msg, calls or {}),
                          without_reranker="hybrid", ranking="lexical")
            if result["mode"] == "passthrough":
                return None
            where = new_capture_path()
            with open(where, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            sweep_captures()
            parts = [MARKER, result["text"]]
            if result["follow_up"]:
                parts.append(result["follow_up"])
            parts.append("full output: %s" % where)
            text = SEPARATOR.join(parts)
        except Exception:                            # noqa: BLE001 -- never cost the output
            logger.warning("the inspeximus digest failed; the result stays whole", exc_info=True)
            return None
        self.digests += 1
        self.bytes_in += len(content)
        self.bytes_kept += len(text)
        return text

    @staticmethod
    def _intent(msg: dict[str, Any], calls: dict[str, str]) -> str:
        """What the digest ranks against: the arguments of the call, else the tool's name.

        The precedence inside the arguments is the shipped PostToolUse hook's own
        (context_saver.post_tool_use_replacement): what the call was for, then what it ran.
        """
        import json

        raw = calls.get(str(msg.get("tool_call_id") or ""), "")
        try:
            args = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        if isinstance(args, dict):
            for key in ("description", "command", "query", "path", "file_path", "pattern", "code"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:300]
        return str(msg.get("tool_name") or "")

    # --- keeping Hermes's own pass from undoing the digest ------------------------------

    @staticmethod
    def _demote_tool_result_at(
        result: list[dict[str, Any]], idx: int, call_id_to_tool: dict[str, tuple[str, str]],
        min_prune_chars: int, protected_skills: Optional[set[str]] = None,
    ) -> bool:
        """Spare a digest on the proactive pass; yield to the pressure pass.

        Hermes marks the pressure pass by passing protected_skills=None, the same signal its own
        skill guard reads. A digest demoted by the proactive pass measured 0.0% of the buried
        question terms, which is the whole reason this override exists.
        """
        msg = result[idx] if 0 <= idx < len(result) else {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if protected_skills is not None and isinstance(content, str) and content.startswith(MARKER):
            return False
        return ContextCompressor._demote_tool_result_at(
            result, idx, call_id_to_tool, min_prune_chars, protected_skills)

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status.update({
            "inspeximus_digests": self.digests,
            "inspeximus_bytes_in": self.bytes_in,
            "inspeximus_bytes_kept": self.bytes_kept,
        })
        return status


def register(ctx) -> None:
    """Hermes plugin entry point: hand the host one engine instance."""
    ctx.register_context_engine(InspeximusContextEngine())
