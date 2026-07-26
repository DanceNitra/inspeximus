<!-- moved out of README.md to keep the landing page readable; content unchanged -->

## The `second_brain` thinking layer

<details>
<summary><strong>Optional add-on</strong> — a separate MCP server that reasons over a folder of Markdown notes. Click to expand.</summary>

`mcp` gives an agent **memory**. `second_brain_mcp` gives it a **second brain to think over** —
point it at any folder of Markdown notes (an Obsidian vault, a Zettelkasten, a `docs/` tree) and an
MCP client (Claude Desktop, Claude Code, Cursor, your own agent) gets the substrate to *reason
against* those notes: pull what's relevant, find where the network is blind, surface non-obvious
bridges, isolate the claims worth checking, and generate ideas by named methods.

**The split that keeps it honest.** The server returns **retrieval + structure**; the calling LLM does
the **reasoning**. The tool is the memory and the map; the agent is the mind. There is no LLM call
inside this server — it scores, links, and slices your notes, then hands the material back. So the
claims below are about what an *agent* did with the tools, not about the tool "thinking" on its own.
No autonomous oracle.

**Runs today, zero config.** It indexes your notes into an in-process `inspeximus` store at startup; with
no embedder it uses the lexical-overlap fallback. An embedder (`INSPEXIMUS_EMBED_URL/MODEL/KEY`) is optional
and matters **at scale**: at several-thousand-note scale, lexical recall@5 decays from 0.94 (small store) to
**0.25** at full corpus while semantic **holds ~0.65** — ≈2.6× (Agora Lab `b4c260`); on paraphrase
queries semantic recall@5 is **0.86 vs 0.20** lexical (`3501f1`).

```
NOTES_DIR=/path/to/your/vault python second_brain_mcp.py      # run after a flat download of both files
```

### See it run (no setup)

A session in text:

```text
▸ relevant_notes("how does feedback speed up learning", k=3)
  → Deliberate Practice (Learning)   relevance 0.60
  → Expected Value     (Decisions)   relevance 0.20

▸ find_gaps()              → isolated: ["Sourdough Starter"]   (the one note with no [[links]])

▸ bridge_candidates("Deliberate Practice")
  → Habit Loops (Habits, DISTANT domain)   — both turn on "feedback latency", and nothing links them

▸ extract_claims("Deliberate Practice")
  → "Feedback latency is the hidden variable: the longer the gap between an action
     and its feedback, the slower the learning."   (line 3 — go ground or challenge it)

▸ idea_methods()           → 10 recipes (Hidden-Connection Bridge, Missing-Reciprocity, …)
```

That `bridge_candidates` hit is the point: a connection across two folders that *you never linked* —
the agent now writes the mapping (or rejects it). The tool found the material; the agent does the thinking.

Register it with an MCP client (point `args` at the file's absolute path so `inspeximus.py`, which sits
beside it, is found):

```json
{
  "mcpServers": {
    "second_brain": {
      "command": "python",
      "args": ["/abs/path/to/second_brain_mcp.py"],
      "env": {
        "NOTES_DIR": "/abs/path/to/your/vault",
        "SECOND_BRAIN_INDEX": "/abs/path/to/second_brain_index.json"
      }
    }
  }
}
```

| tool | returns |
|---|---|
| `index_status` | notes indexed, folder spread, resolved `NOTES_DIR` (call first; `0` ⇒ fix `NOTES_DIR`) |
| `relevant_notes` | the `k` most relevant notes by relevance × accrued value (value accrues with use; a cold index is effectively relevance-ranked), with excerpts |
| `coverage_gap` | the **negative space** of a question: top notes + a measured completeness score + the explicit sub-terms with **no** supporting note — a WYSIATI guard so the agent sees what's *missing* and doesn't answer a tidy-but-incomplete context with false confidence |
| `find_gaps` | isolated/under-linked notes + thin folders — where the network is blind (noisy on a tiny vault; earns its keep at scale) |
| `bridge_candidates` | distant notes (different folder, no link) that are semantically close = candidate connections; the agent writes or rejects the mapping |
| `extract_claims` | claim-like sentences from a note so the agent can ground or challenge them |
| `idea_methods` | a toolkit of named idea-generation recipes, so generation is principled, not a vibe |

Dogfood result, stated honestly: pointed at the maintainer's own 10,000-note vault, an agent using
these tools caught a number in his *own* forecasting note inflated ~7× ("60-78%" vs the real ~6-11%),
surfaced two silently-contradicting notes, and proposed ideas via `idea_methods` — two of which were
then severe-tested **in Agora's separate research lab** (not inside this server) and held. The LLM did
the reasoning; the corrections still warrant a source-check before public citation.

### Trust & safety
- **Read-only over your notes.** The server reads `NOTES_DIR` recursively; it does no `eval`, no shell,
  no subprocess, and writes only its own index file. Symlinks/junctions that point *outside*
  `NOTES_DIR` are deliberately **not** followed (so a planted link in a shared/cloned vault can't leak
  files from elsewhere on disk).
- **The embedder is a trust boundary.** If you set `INSPEXIMUS_EMBED_URL`, the **full text of every note**
  is POSTed there. It's validated at startup — `https` anywhere, plain `http` only to loopback (local
  Ollama, etc.), and cloud-metadata/link-local targets are refused. Point it only at an endpoint you trust.
- **Notes over ~2 MB are skipped** (configurable via `SECOND_BRAIN_MAX_BYTES`) so a single huge file
  can't exhaust memory.

</details>

