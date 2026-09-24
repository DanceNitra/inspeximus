# Onboarding review: the README, followed cold

Date: 2026-09-24. README at `origin/main` 93518d1 (its README is byte-identical to 21b6cdd's). Line numbers
below are README.md line numbers at that commit.

The reviewer acted as a developer who had never seen inspeximus. They followed only README.md, top to
bottom, and ran every fenced code block exactly as written. No library code was changed. This file is the
only change on the branch.

**Environment.** Linux, Python 3.11.15, a fresh venv, a fresh `HOME`, an empty working directory and
`env -i` (only the proxy and CA variables were kept). `pip install inspeximus` resolved to **3.9.0** from
PyPI. `main` is at 3.9.1, so PyPI trails the README by one release. Outbound network ran under a
restrictive egress policy: pypi.org, raw.githubusercontent.com and git-over-HTTPS to this repository were
reachable, and most other hosts were refused. Anything that depended on a refused host is marked
**NOT CHECKED** with the host.

**How each Python block was run.** Each block was run twice. First it was saved as a file and run with
`python block.py`. Then it was fed to an interactive interpreter (`python -q -i < block.py`), which echoes
expression values the way the README's `# ...` comments assume. Block L323 was also pasted into a real
3.11 REPL on a pseudo-terminal, to check its `with` blocks survive a paste. Each block was run in two
layouts: in its own empty directory, and one after another in a single shared directory (what "top to
bottom" does if you keep one folder).

---

## Summary: what a newcomer hits, most costly first

1. **The code blocks are not independent, and in order they give wrong answers.** Six blocks all open
   `Inspeximus("memory.json")`. The first example ends on `revert()`. When the next block writes db-7
   again, the echo guard retires that write as a stale restatement and gives no warning. Run top to bottom
   in one folder, the headline "30 seconds" block (L175) recalls **db-3** where it promises db-7 "every
   time". The receipts block (L230) prints `False` where it promises `True`. The action-ledger block
   (L323) deploys to db-3 twice. Every one of these blocks is correct in an empty folder.
2. **Two steps need `cryptography`, and the README never says so.** The signing block (L729) and example
   `06_gdpr_erasure_receipt.py` raise `RuntimeError` on a plain `pip install inspeximus`. The extra that
   fixes this, `inspeximus[crypto]`, exists in pyproject.toml but appears nowhere in the README. L720 says
   the four standards are emitted "with no dependencies".
3. **Run as scripts, the Python blocks print nothing.** Results appear only as comments. The first Python
   block with a `print()` is at L614, 70% of the way down the page.
4. **The fastest first result is not in the README.** `inspeximus demo` (shipped in 3.9.0) runs three
   checks in 0.2 s and leaves no files. The README never mentions it. The reviewer found it in the git
   log, not by following the README.
5. **Both Claude Code routes silently need `uv`, and the shell route installs no hooks.** The plugin's
   MCP server and all five hook commands launch through `uvx`. `inspeximus install --ide claude` writes
   only an `mcpServers` entry. That contradicts "Both wire an MCP server with 133 tools and the same
   hooks" (L664).
6. **The key-comparison one-liner (L594) raises a false alarm when a fetch fails.** `curl -s` hides the
   error, so an empty download shows up as a key mismatch. The README tells that reader to distrust both
   copies and open an issue.
7. **On PyPI, the README's 21 relative link targets (27 occurrences) do not resolve.** That includes the
   correction chart at L43–44. The same README is the PyPI long description.

---

## Time to first meaningful result

| Path | Result | Time |
|---|---|---|
| `pip install inspeximus` (L28) | 3.9.0 installed, no dependencies pulled | 1.2 s |
| First example (L32) saved as a file and run | exit 0, **prints nothing** | not a result |
| First example (L32) pasted into a REPL | `'The staging database is db-7.internal'` | **34.9 s** wall clock from an empty directory (clock started at venv creation; most of it is the reviewer's time between commands; install plus run is under 3 s of compute) |
| First printed result when blocks are run as scripts | L131 clone plus benchmark: `store-resolved=1.00 (resolved=20 both=0 stale=0 neither=0, n=20)` | 7.1 s for that step (clone of 2,031 files plus a 0.1 s probe) |
| `inspeximus demo` (not in the README) | three claims checked, `All three checks passed in 0.04 s.` | 0.2 s after install |

The first meaningful result arrives quickly **if** the reader knows to use a REPL. The page never says
so. A reader who saves and runs the first example sees a silent exit 0.

---

## Step table

| Step | What happened | Fix proposal |
|---|---|---|
| **L28** `pip install inspeximus` | OK. 1.2 s, exit 0. Installed 3.9.0 and nothing else. `main` describes 3.9.1. | None needed. Put `inspeximus demo` on the next line (see "demo" below). |
| **L32** first example | **Script:** exit 0, no output. The results are only in comments. **REPL:** matches, `recall(...)[0]["text"]` → `'The staging database is db-7.internal'`. Each `remember` also echoes an id (`'6e64dd9f18'`), and `revert` echoes a dict; neither is shown. The block ends on `revert()`, so it leaves `memory.json` with db-3 active and db-7 retired. That state breaks L175, L230 and L323 when they reuse the file (below). | Say "paste into a Python REPL", or `print()` the recall line. Give the block its own file (`Inspeximus("quickstart.json")`), or say the revert changes state. |
| **L131** clone plus `integrity_bench_store_resolves.py` | OK. Prints the promised line exactly. Clone plus run took 7.1 s; the probe alone took 0.105 s ("milliseconds" holds). Side effects: (a) the block `cd`s into the checkout, so every later block runs inside it, where `import inspeximus` resolves to the checkout's source (3.9.1), not the pip-installed 3.9.0; (b) the probe rewrites the tracked `probes/integrity_bench_store_resolves_result.json`, which leaves the clone dirty. | Say the probe checks the checkout, not the installed wheel, or add `cd ..` after it. Write run output to an untracked path. |
| **L175** "The 30 seconds that matter" | **Empty directory, REPL:** matches every comment (db-7, then db-3 after `revert`). **Script:** no output. **After L32 in the same folder:** `recall` returns `'The staging database is db-3.internal'` where the block promises db-7 "every time". The cause was confirmed with `m.last_write` after the db-7 write: `'blocked': True, 'policy': 'echo_guard', 'note': 'this asserted a value that was already superseded for this key ...'`. Nothing is printed or raised. | Give each block its own store file, or open the section with "each example starts from an empty store". It is also worth one sentence that re-running this block on the same file shows the echo guard at work, since that is the product's point. |
| **L230** receipts and tamper | **Empty directory:** matches both comments (`True`; `'its TEXT or KEY no longer matches its write receipt (edited after write)'`). The REPL also echoes a roughly 1.5 KB snapshot dict from `sqlite_store.save(...)`, which is not shown. **Shared folder:** `verify_writes()[0]` is `False`, because earlier records have no receipts. The last line prints a different message: `"['90e4437cd1', ...] .... They were inserted out of band, or written while receipts were off. Pass coverage_strict=False ..."`. | Use its own file. Write `_ = sqlite_store.save(...)` so the REPL stays quiet. |
| **L258** `older.json`, `enable_receipts()` | OK in both layouts. Output: `'write receipts are DISABLED'`, `1`, `True`. Script: no output. | None needed. |
| **L281** `rota.json`, `retire` | OK. `m.current("on-call")` shows nothing in a REPL (that is how `None` displays), and `retire` echoes a dict. Both are harmless. | None needed. |
| **L299** two handles on `crew.json` | OK. `['record.added', 'plan.updated']`, then `'The plan is: ship on Friday'`. `publish_event` echoes `2`. | None needed. |
| **L323** `ActionLedger` | **Empty directory:** matches (`'superseded'`, `'active'`, `(True, [])`). A paste into a real 3.11 REPL works, because the blank lines after the `with` bodies are present. **Shared folder:** the second deploy also targets db-3, and `what_it_knew(0)` reports `'active'`, not `'superseded'`. Same echo-guard cause as L175. | Use its own file. |
| **L532** `witness watch` | `pip install` is a no-op. `witness watch` exits 1 with `could not read https://dancenitra.github.io/inspeximus-log/log: <urlopen error Tunnel connection failed: 403 Forbidden>`. The refusal came from this environment's egress policy, not from the README. The error is clear. **NOT CHECKED** end to end (dancenitra.github.io). | None from this run. |
| **L548** `ots upgrade` / `ots verify` | Cannot run as written. The `<the .ots receipt>` placeholders are shell redirections: `syntax error near unexpected token 'newline'`, exit 2. The README never says where a real `.ots` receipt and its stamped file are. `inspeximus ots verify --help` works. | Give one copy-pasteable pair (a published head, its `.ots`, and a real block header). Use `RECEIPT.ots`-style placeholders rather than `<...>`, which bash parses as redirection. |
| **L581** verifier-key line and its SHA-256 (L586) | Not a command. The stated hash matches only without a trailing newline: `printf '%s' LINE \| sha256sum` gives `1017ff22…` (match), while `echo LINE \| sha256sum` gives `434658c8…`. | Say "without a trailing newline", or give the `printf` command. |
| **L594** `diff <(curl …) <(curl …)` | Exit 1, output `0a1 > 92.5.74.17.sslip.io/log+41dfe27a+…`, which reads as "the two copies differ". Cause: the github.io fetch was refused here, and `curl -s` prints nothing on failure. The raw.githubusercontent.com side returned the correct line. Following L590, a reader would distrust both keys and open an issue. The actual comparison is **NOT CHECKED** (dancenitra.github.io; `inspeximus-log` is outside this review's scope). | Use `curl -fsS` on both sides so a failed fetch is an error, not a diff. |
| **L614** "Put it under a real agent" | OK. Script and REPL both print `user prefers dark mode`. This is the first block that prints anything when run as a script. | Make the top example look like this one (it prints). |
| **L653** `/plugin marketplace add` and `/plugin install` | Run through the equivalent Claude Code CLI commands (`claude plugin marketplace add DanceNitra/inspeximus`, `claude plugin install inspeximus@inspeximus`, CLI 2.1.281, fresh HOME). Both succeed. The plugin's MCP server (`.mcp.json`) and all five hook commands (`hooks/hooks.json`) run `uvx …`. In the clean environment `uvx` is not on PATH, so none of them can start. With `uv` on PATH, `uvx --from inspeximus[mcp] inspeximus-mcp` initialised in 4.9 s on a cold start and listed **133** tools, which matches L664. | Add "requires `uv`" next to "no pip, no config file". |
| **L660** `inspeximus install --ide claude` | Exit 0. It wrote `~/.claude.json` with `mcpServers.inspeximus` = `uvx --from inspeximus[mcp] inspeximus-mcp`. (a) **No hooks are written**, yet L664 says both routes wire "the same hooks". Hooks come from `python -m inspeximus.claude_code --install`, per that module's docstring, which the README does not mention. (b) The server is fetched by `uvx`, so the `pip install` step above does not feed it, and the command did not warn that `uvx` was missing. | Fix the L664 sentence or have `install` write the hooks. Warn when `uvx` is not on PATH. |
| **L664–669** hook list | Lists three hooks. The plugin registers five events: it also has `UserPromptSubmit` and `SessionEnd`. | List all five, or say "including". |
| **L729** `transparent_statement` | **Fails on a plain install:** `RuntimeError: signing write receipts needs the cryptography package (pip install cryptography)` at `new_receipt_keypair()`. In a REPL, the following lines then fail with `NameError`. After `pip install cryptography` it runs and prints nothing, because the result is assigned to `doc`. `verify_transparent_statement` is imported but never used. Its signature (`statement, verify_statement, verify_receipt_sig, leaf, expected_root, …`) cannot be guessed. | Put `pip install "inspeximus[crypto]"` above the block. Show the verify call. Change "with no dependencies" (L720) to "no dependencies except `cryptography` for signing". |
| **L803** `python claims_audit.py` (inside the clone) | OK. Exit 0. It audits the PyPI 3.9.0 wheel (sha256 `70073387…`): `13 passed · 0 FAILED`, then the promised last line. It took **1.2 s**; the README says "Forty seconds". L819 promises "a handful of rows marked WITHDRAWN", but the run shows only the count `WITHDRAWN 2`, not the rows. | Update the timing. Print the withdrawn rows, or reword L819. |
| **L811** expected output | Matches L803's last line exactly. | None needed. |
| **L824** `integrity_bench_revert.py --judge local --n 5` | OK. 0.13 s; `inspeximus 1.00 (A=5 B=0 other=0 err=0, n=5)`. It prints its local-judge caveat as promised. L829 points at "the OpenAI-judged figures in the table above", but no table above holds revert figures. It rewrites the tracked `probes/integrity_bench_revert_result_localjudge.json`. | Link to where the OpenAI-judged revert figures live. Write output to an untracked path. |
| **L636–639** examples 01, 02, 03, 06 (the README says to run them) | 01, 02 and 03 run offline and print what they did. **06 fails** without `cryptography` (same `RuntimeError`) and passes once it is installed. | Mark 06 "needs `inspeximus[crypto]`". |
| **Inline CLI commands** in the prose (L165–L697, 22 run) | Most run as written against a fresh store. Three do not. `actions export-trail` (L390) exits 2 with `export-trail needs --out, --agent-id and --agent-version`. `actions lifecycle decommission --disposition erased` (L394) exits 2 because `--actor` is required. `partitions close NAME --actor` (L400) is shorthand and exits 2. The CLI's default store is `inspeximus_memory.json`, while every Python block writes `memory.json`, so `inspeximus compliance` after the Python blocks reads a different, empty store. | Show the full arguments. Say `--path memory.json` once. |
| **"demo"** | The README has no demo command. The only thing it calls "the demo" is the L175 block ("The demo above ends at `revert()`", L608). `inspeximus demo` exists (the 3.9.0 changelog) and ran in 0.2 s: correction held, erasure certificate valid, tamper refused, all PASS, and it leaves no files behind. | Put `inspeximus demo` directly under `pip install inspeximus` at L28. It is the best first run in the package and needs no REPL, no clone and no file. |
| **L143–145** | Two consecutive `---` rules. | Delete one. |

---

## Terms used before they are explained

| Term | First used | Explained | Proposal |
|---|---|---|---|
| guard / "guard disabled" | L44 (chart alt text), L97 table | Behaviour at L194–199. Named "echo guard" only at L215. | Name it at first use: "the echo guard, which refuses a restated retired value". |
| echo, "echo defense", "echo-attributable" | L102–108 | Never defined as a word. The behaviour is at L194. | Define "echo" at L88, where the receipts start. |
| tombstone | L50 | L455 ("so a later reader can tell a deliberate erasure from tampering") | Half a sentence at L50. |
| subject (`forget_subject`) | L50 | L360 hints that "source resolves to the subject". No block ever writes a record with a subject. | Show one `remember(..., source=...)` that `forget_subject` would find. |
| draft-sharif-agent-audit-trail-04 | L53 | Never. L391 only calls it an IETF draft. | Say it is an IETF Internet-Draft for agent audit trails, and link it. |
| MCP | L5 | Never expanded | "Model Context Protocol" once. |
| judge (LLM judge) | L116 | L825 (`--judge local`) | One clause at L116. |
| store-resolved, both, stale, neither | L136 (printed line) | Only behind the INTEGRITY_BENCHMARK link | Gloss the four counters inline. |
| signed record on "every write" | L149 | L795 says receipts are **off by default**. Signing needs a key and `cryptography`. | L149 overstates the default. Say "with receipts on". |
| Annex III | L152 | Never | "(the high-risk use-case list)". |
| RAMR | L102 | Never (a link only) | Expand the acronym. |
| MemTX corpus | L213 | Never, and no link | Link it. |
| `derived_from`, `authority` | L209–211 | Never shown in code | A two-line example. |
| Merkle root, backfill | L254 | Merkle is assumed knowledge; backfill is explained in the same sentence | Acceptable. |
| L1 | L296 | Never (it means an in-process cache) | Say "in-process cache". |
| tenant | L296 | L714 (`for_tenant`) | Move `for_tenant` up, or add "(see Multi-tenant isolation)". |
| salt file | L348 | Never named. The file is `memory.json.actions.json.salt`. | Name the file. |
| CNIL | L397 | Never | "the French data-protection authority". |
| StoreChangedOnDisk | L431 | Never | Say it is an exception the store raises on a concurrent write. |
| COSE key set, C2SP static-ct-api | L494–497 | Never | A link each. |
| signed-note, verifier-key line format | L581–591 | L591 links c2sp.org, and only for the key id | One sentence on the line's fields. |
| CML contract | L712 | Never, and no link | Link or expand. |
| SCITT, COSE_Sign1, Receipt of Inclusion | L725–737 | Standard numbers only | Acceptable for this section's audience. |
| k-of-n co-signatures, `witnessed_head()` | L746 | The witness concept at L517–528, not this API | One example call. |
| memory.json is SQLite, not JSON | L35 (the name) | L406 ("A new store is written as rows"); L241 uses `sqlite_store` first | Say so at the first example. A reader who opens `memory.json` finds a binary file. |

---

## Places I had to guess

1. Whether to run the blocks in a REPL or as scripts. Nothing says so; the comments assume a REPL.
2. Whether each block needs a fresh folder. It does (Step table, L175, L230, L323).
3. Whether the first example's final `revert()` leaves state that matters. It does.
4. Which install fixes the `cryptography` error: `pip install cryptography` (the message) or
   `inspeximus[crypto]` (pyproject.toml). The README names neither.
5. How to verify the statement from L729. `verify_transparent_statement` is imported and never shown.
6. Which files `ots upgrade` and `ots verify` should be pointed at (L548).
7. Whether the SHA-256 at L586 includes a newline. It does not.
8. That the Claude Code routes need `uv`, and how to get the hooks when installing from the shell.
9. Which store the CLI reads (`inspeximus_memory.json`) compared with the Python examples (`memory.json`).
10. The missing arguments for `actions export-trail` and `actions lifecycle decommission`.
11. Which package the L131 and L824 probes test. They import the checkout, not the installed wheel.
12. Which table L829's "OpenAI-judged figures in the table above" refers to.
13. Where the WITHDRAWN rows are that L819 says the audit will show.
14. What "the demo" is. L608 says "The demo above"; the actual `inspeximus demo` is never mentioned.

---

## Link check

Every link, image and badge target in README.md was extracted: 56 unique targets, 33 external. Relative
paths were resolved against the repository, and anchors against the README's own headings.

| Target | Line(s) | Result |
|---|---|---|
| 21 relative files: `docs/DEEP_DIVE.md`, `docs/assets/correction-dark.svg`, `docs/assets/correction-light.svg`, `LICENSE`, `probes/INTEGRITY_BENCHMARK.md`, `docs/AI_ACT.md`, `probes/does_a_restatement_take_the_key_back.py`, `docs/INTEGRATIONS.md`, `examples/01_basics.py`, `examples/02_correction_and_erasure.py`, `examples/03_semantic_recall.py`, `examples/06_gdpr_erasure_receipt.py`, `docs/CORE_MAP.md`, `docs/integration_conformance.json`, `docs/CLAIMS.md`, `docs/API.md`, `docs/ERASURE.md`, `MCP_LISTINGS.md`, `examples/`, `CHANGELOG.md`, `CITATION.cff` | 9–879 | OK on GitHub: all exist. **Broken on pypi.org:** the 3.9.0 long description carries these 21 targets (27 occurrences) as relative paths, which PyPI does not resolve. That includes the chart images at L43–44. |
| `#how-this-is-tested`, `#check-us-without-trusting-us` | 24, 645 | OK: both headings exist. |
| https://pypi.org/project/inspeximus/ | 15, 18, 22, 23 | OK (200) |
| https://raw.githubusercontent.com/DanceNitra/inspeximus/main/docs/assets/hero.jpg | 3 | OK (200, image/jpeg, 191,359 bytes) |
| https://raw.githubusercontent.com/DanceNitra/inspeximus/main/README.md (in the L594 command) | 595 | OK (200) |
| https://dancenitra.github.io/inspeximus/ plus `quickstart.html`, `compare.html`, `migrate-from-mem0.html`, `ai-act.html`, `claude-code.html`, `transparency/`, `erasure.html`, `audit-trail.html` | 8–14, 83, 166, 501, 838–846 | **NOT CHECKED** (host: dancenitra.github.io). The source file of every page is present in this repository and listed in `sitemap.xml`. |
| https://dancenitra.github.io/inspeximus-log/log/checkpoint.vkey, and `…/inspeximus-log/log` in the L534 command | 534, 589, 595 | **NOT CHECKED** (host: dancenitra.github.io; separate repository outside this review's scope) |
| https://github.com/DanceNitra/inspeximus/actions/workflows/ci.yml, `audit.yml`, and both `badge.svg` | 20, 21 | **NOT CHECKED** (host: github.com). `.github/workflows/ci.yml` and `audit.yml` exist in the repository. |
| https://img.shields.io/… (6 badges: pypi version, downloads, pyversions, dependencies, tests, license) | 18, 19, 22, 23, 24, 25 | **NOT CHECKED** (host: img.shields.io) |
| https://pypistats.org/packages/inspeximus | 19 | **NOT CHECKED** (host: pypistats.org) |
| https://zenodo.org/badge/DOI/10.5281/zenodo.21708778.svg | 26 | **NOT CHECKED** (host: zenodo.org) |
| https://doi.org/10.5281/zenodo.21708778 | 26, 878 | **NOT CHECKED** (host: doi.org) |
| https://github.com/agno-agi/agno/blob/main/cookbook/11_memory/integrations/inspeximus_integration.py | 71 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/agno-agi/agno/pull/10146 | 72 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/tech4biz-yasha/agmi | 76, 786 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/tech4biz-yasha/agmi/blob/main/agmi/adapters/inspeximus_rows.py | 78 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/tech4biz-yasha/agmi/pull/1 | 79 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/DanceNitra/ramr | 102 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://github.com/DanceNitra/agora | 884 | **NOT CHECKED** (host: github.com; repository outside review scope) |
| https://c2sp.org/signed-note | 591 | **NOT CHECKED** (host: c2sp.org) |
| https://freetsa.org/tsr (inline command) | 384 | **NOT CHECKED** (host: freetsa.org) |

Totals for the 56 link targets: 23 in-repository targets resolve (21 files, 2 anchors), 2 external links
answer 200, and 31 external links are **NOT CHECKED** from this environment. The three URLs that appear
only inside commands (L384, L534, L595) are extra to these counts. Fix proposal for PyPI: make the README's relative links absolute, as
`hero.jpg` at L3 already is (`raw.githubusercontent.com/...` for images, `github.com/.../blob/main/...`
for files). Then the same README reads correctly on GitHub and on PyPI.

---

## Incidental, outside the README path

- `inspeximus residue --root DIR ...` on a path that does not exist warns "nothing was searched" and exits
  1. That is right. But `residue-verify` on the resulting certificate prints `STORE AT SCAN TIME: residue
  found` and `DOCUMENT: valid`, with `FAIL signed` above them, and exits 0. For a scan that searched
  nothing, "residue found" is the wrong label.
- Every receipted store also writes a chain head under `~/.config/inspeximus/heads/`, outside the working
  directory. L791 says so, but a reader of the early examples will not expect files outside their folder.

## Reproducing this review

```bash
python3 -m venv venv && . venv/bin/activate && pip install inspeximus   # 3.9.0 on 2026-09-24
# Each Python block, in its own empty directory, both ways:
python block.py            # as a script
python -q -i < block.py    # as a REPL would echo it
# The shared-folder failure. Save the L32 and L175 blocks as L32.py and L175.py, then:
mkdir shared && cd shared
python -q -i < ../L32.py && python -q -i < ../L175.py   # L175's recall returns db-3, not db-7
python -c "from inspeximus import Inspeximus; m=Inspeximus('memory.json'); m.remember('The staging database is db-7.internal', key='staging-db'); print(m.last_write['policy'], m.last_write['blocked'])"   # echo_guard True
```
