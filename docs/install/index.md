# Install inspeximus: one memory for all your AI agents

This page is written so an AI agent can follow it for you. It installs inspeximus once and connects every
AI agent on this computer to the same memory: Hermes Agent, Claude Code, Gemini CLI, Codex, Antigravity,
Cursor, Devin and Windsurf, and Cline. A decision recorded in one agent is recalled in the others.

## For an agent following this page

Before you run anything, ask the user two questions and wait for both answers:

1. "Some of your agents don't read instructions from a memory server. Can I add one line to their rules
   files telling them to check the shared memory at the start of each task?"
2. "Can I connect Hermes Agent to this shared memory? That installs inspeximus into Hermes and makes it
   Hermes' memory provider, replacing the provider Hermes uses now, if it has one." Ask this only if the
   user has Hermes Agent. Otherwise the answer is no.

Then run the commands for your operating system. Replace `RULES_ANSWER` with `yes` or `no` from the answer
to question 1, and `HERMES_ANSWER` with `yes` or `no` from the answer to question 2. Change nothing else.
If a placeholder is left in, the installer stops with an error and changes nothing. The installer never
asks a question of its own. When you finish, show the user the table the installer printed and tell them
to restart each app it lists as wired.

If you are Hermes Agent, the installer changes your own memory provider. This session keeps working as it
is; the shared memory takes effect after the user restarts Hermes.

<a id="linux-macos"></a>
## Linux and macOS

Requires Python 3.9 or later.

<!-- ci: posix -->
```bash
python3 -m venv ~/.inspeximus/venv
~/.inspeximus/venv/bin/python -m pip install "inspeximus[mcp]"
~/.inspeximus/venv/bin/inspeximus install --all --rules RULES_ANSWER --hermes-provider HERMES_ANSWER
```

<a id="windows"></a>
## Windows (PowerShell)

Requires Python 3.9 or later.

<!-- ci: windows -->
```powershell
python -m venv "$HOME\.inspeximus\venv"
& "$HOME\.inspeximus\venv\Scripts\python.exe" -m pip install "inspeximus[mcp]"
& "$HOME\.inspeximus\venv\Scripts\inspeximus.exe" install --all --rules RULES_ANSWER --hermes-provider HERMES_ANSWER
```

The virtual environment lives in `~/.inspeximus/venv` on purpose. Every agent's configuration names the
Python in it, so it must stay where it is. To update later, run the same `pip install -U "inspeximus[mcp]"`
and `install --all` again: every agent is pinned to the version that wrote its configuration.

## What `install --all` does

It looks for each agent that is installed on this computer: its command on the PATH, or its app in the
usual install location. A configuration folder left behind by an uninstalled app does not count. It
registers the inspeximus memory in every agent it finds, and points all of them at one store,
`~/.inspeximus/coding_memory.json`. If your agents already point at one existing store, that store is kept.
If they point at different stores, it stops and asks you to choose one with `--store <path>`. It prints
one table:

```text
host                     found  wired                store path                                recall
Hermes Agent (~/.hermes) yes    provider inspeximus  /home/you/.inspeximus/coding_memory.json  provider
Claude Code              yes    add                  /home/you/.inspeximus/coding_memory.json  hooks
Gemini CLI               yes    create               /home/you/.inspeximus/coding_memory.json  instructions
...
```

It never replaces your other settings in an agent's configuration, keeps a `.bak` copy of every file it
changes, and records one decision in the shared memory, so the next session in any agent can confirm the
memory works. If the current project had an older Claude Code memory in `.inspeximus/coding_memory.json`,
its records are imported into the shared store.

## Per agent

<a id="hermes-agent"></a>
### Hermes Agent

Hermes must be installed with its official installer. The `hermes-agent` package on PyPI (0.19.0) does not
load memory-provider packages, so inspeximus cannot become its memory there.

With `--hermes-provider yes`, the installer finds Hermes' own virtual environment (under `$HERMES_HOME`,
`~/.hermes`, or `%LOCALAPPDATA%\hermes`), installs inspeximus into it, and asks Hermes' own provider loader
whether it lists inspeximus. If it does, the installer sets `memory.provider: inspeximus` in `config.yaml`
and points the provider at the shared store. If it does not, the installer removes what it installed,
changes nothing else, and the table says `cannot load provider` with the fix. With `--hermes-provider no`,
nothing in Hermes is changed and the table says `skipped (no)`. Restart Hermes.

<a id="claude-code"></a>
### Claude Code

Configuration: `~/.claude.json`, plus hooks in `~/.claude/settings.json`. The SessionStart hook recalls the
shared memory at the start of every session. Restart Claude Code. Do not also install the marketplace
plugin, or every hook runs twice.

<a id="gemini-cli"></a>
### Gemini CLI

Configuration: `~/.gemini/settings.json`, key `mcpServers`. Gemini CLI appends the server's instructions to
its system instructions, so it is told to recall at the start of each task. Restart Gemini CLI.

<a id="codex"></a>
### Codex

Configuration: `~/.codex/config.toml`, table `[mcp_servers.inspeximus]`, shared by the Codex CLI and the
Codex app. Codex shows the server's instructions to the model with its tools. Restart Codex.

<a id="antigravity"></a>
### Antigravity

Configuration: `~/.gemini/config/mcp_config.json`. With `--rules yes`, the recall rule goes to
`~/.gemini/config/rules/inspeximus.md` (always on). Restart Antigravity, or refresh in Manage MCP Servers.

<a id="cursor"></a>
### Cursor

Configuration: `~/.cursor/mcp.json`. Cursor keeps global rules in its settings only, so with `--rules yes`
the rule goes to `.cursor/rules/inspeximus.mdc` in the project you run the installer from, not to your home
folder. That file is part of the project, so it can be committed with it; add it to `.gitignore` if you
don't want that. Outside a project, the installer prints the line to paste into Customize > Rules. Restart
Cursor.

<a id="devin"></a>
### Devin Desktop and Devin CLI

Configuration: `~/.config/devin/mcp_config.json` on Linux and macOS (`$XDG_CONFIG_HOME/devin` when that is
set), `%APPDATA%\devin\mcp_config.json` on Windows. Devin Desktop and Devin CLI share this file. With
`--rules yes`, the recall rule is appended to `AGENTS.md` in the same folder, which Devin loads at the start
of every session. Restart Devin Desktop. Versions before v3000.3 read servers from `config.json` instead;
update Devin, which moves them on startup.

<a id="windsurf"></a>
### Windsurf

Windsurf is now Devin Desktop. Its agents read the Devin configuration described in the preceding section,
and the installer writes it. For older Windsurf builds, it also writes `~/.codeium/windsurf/mcp_config.json`.
With `--rules yes`, the recall rule is appended to `~/.codeium/windsurf/memories/global_rules.md` as well.
Restart Windsurf.

<a id="cline"></a>
### Cline

Configuration: `~/.cline/data/settings/cline_mcp_settings.json`. With `--rules yes`, the recall rule goes to
`~/Documents/Cline/Rules/inspeximus.md`. Cline reloads its configuration by itself.

## Check it

In any agent, ask: "What do you remember about the inspeximus setup?" The agent finds the decision the
installer recorded, which names every agent that was wired.

## Sources

Each configuration path and rules location on this page comes from the host's own documentation, read on
2026-09-26. The Hermes loader behaviour was measured on 2026-09-27.

| Agent | What the source gives | Source |
|---|---|---|
| Hermes Agent | `memory.provider` in `~/.hermes/config.yaml` | https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers |
| Hermes Agent | the official installer and Hermes' own virtual environment | https://hermes-agent.nousresearch.com/docs/getting-started/installation |
| Hermes Agent | PyPI `hermes-agent` 0.19.0 lists no provider package; the official install (0.21.3) lists inspeximus | measured with `plugins.memory.list_memory_provider_names()` in each build |
| Claude Code | `~/.claude.json` for MCP servers | https://code.claude.com/docs/en/mcp |
| Gemini CLI | `~/.gemini/settings.json`, `mcpServers`; instructions appended to the system instructions | https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md |
| Codex | `~/.codex/config.toml`, `[mcp_servers.<name>]` | https://learn.chatgpt.com/docs/extend/mcp |
| Codex | server instructions go into the tool description the model sees | https://github.com/openai/codex/blob/75e0e0aad97a86138b8b1ec87d9b544b4a35ecbf/codex-rs/codex-mcp/src/rmcp_client.rs |
| Antigravity | `~/.gemini/config/mcp_config.json` | https://antigravity.google/docs/mcp |
| Antigravity | `~/.gemini/config/rules/*.md`, `trigger: always_on` | https://antigravity.google/docs/rules |
| Cursor | `~/.cursor/mcp.json` | https://cursor.com/docs/mcp |
| Cursor | `.cursor/rules/*.mdc` with `alwaysApply`; global rules in settings only | https://cursor.com/docs/rules |
| Devin Desktop, Devin CLI | `~/.config/devin/mcp_config.json`, `%APPDATA%\devin\mcp_config.json`; v3000.3 change | https://docs.devin.ai/cli/extensibility/mcp/configuration |
| Devin Desktop, Devin CLI | global rules in `AGENTS.md` in the same folder | https://docs.devin.ai/cli/extensibility/rules |
| Windsurf | Cascade reads the Devin file; `~/.codeium/windsurf/mcp_config.json` is the editor's discovery file | https://docs.devin.ai/windsurf/plugins/cascade/mcp |
| Windsurf | `~/.codeium/windsurf/memories/global_rules.md` | https://docs.devin.ai/desktop/cascade/memories |
| Cline | `~/.cline/data/settings/cline_mcp_settings.json` | https://docs.cline.bot/getting-started/config |
| Cline | `~/Documents/Cline/Rules` | https://docs.cline.bot/customization/cline-rules |
