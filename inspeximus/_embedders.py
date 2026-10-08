"""The embedder the MCP server and the CLI share (3.17.0, AUDIT-A P-4).

`inspeximus reembed` is the one action the 3.17.0 release asks of every user with an embedder, and it built its own
embedder: no `embed_id`, so the vectors it wrote carried no recipe stamp and `<store>.embedid` was never written, no
query embedder, so a nomic model got no task prefixes, and a plain run replaced nothing after a model change. It now
builds the embedder here, the way the MCP server does, so the two cannot drift.
"""
from __future__ import annotations

import os


def make_embedders(default_model: str = "text-embedding-3-small"):
    """Optional OpenAI-compatible embedder (zero extra deps, urllib). Returns `(embed_doc, embed_query, embed_id)`.

    For nomic-embed-text (asymmetric, trained with task prefixes) it returns SEPARATE document and query embedders that
    prefix `search_document: ` and `search_query: `, measured on LoCoMo (n=1536) to lift recall_any@1 from 0.19 to 0.29,
    with the recipe `<model>|nomic-sd-sq`. For symmetric models it returns `(embed, None, model)`.
    `(None, None, None)` when `INSPEXIMUS_EMBED_URL` is unset or names a host the user's config does not allow."""
    from ._http import embedding_from, env_key, env_url, post_json   # no redirect, no proxy for loopback (3.16.4)
    url = env_url("INSPEXIMUS_EMBED_URL")             # another host only when the user's config allows it (F-12)
    if not url:
        return None, None, None
    model = os.environ.get("INSPEXIMUS_EMBED_MODEL", default_model).strip()
    key = env_key("INSPEXIMUS_EMBED_KEY", url)

    def _embed(text: str, prefix: str = ""):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return embedding_from(post_json(url, {"model": model, "input": prefix + text}, headers, 20))

    # nomic-embed-text is asymmetric; task prefixes are REQUIRED for good retrieval. Opt out with INSPEXIMUS_NOMIC_PREFIX=0.
    if "nomic" in model.lower() and os.environ.get("INSPEXIMUS_NOMIC_PREFIX", "1") != "0":
        return (lambda t: _embed(t, "search_document: ")), (lambda t: _embed(t, "search_query: ")), f"{model}|nomic-sd-sq"
    return _embed, None, model
