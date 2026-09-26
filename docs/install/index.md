# Install inspeximus: one memory for all your AI agents

This page is written so an AI agent can follow it for you. It installs inspeximus once and connects every
AI agent on this computer to the same memory: Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor,
Windsurf (Devin Desktop), Devin CLI, Cline, and Hermes Agent. A decision recorded in one agent is recalled in the others.

## For an agent following this page

Before you run anything, ask the user two questions and wait for both answers:

1. "Some of your agents don't read instructions from a memory server. Can I add one line to their rules
   files telling them to check the shared memory at the start of each task?"
2. "If Hermes Agent already uses another memory provider, can I switch it to inspeximus?" Ask this only if
   the user has Hermes Agent. Otherwise the answer is no.

Then run the commands for your operating system. Replace `RULES_ANSWER` with `yes` or `no` from the answer
to question 1, and `HERMES_ANSWER` with `yes` or `no` from the answer to question 2. Change nothing else.
If a placeholder is left in, the installer stops with an error and changes nothing. The installer never
asks a question of its own. When you finish, tell the user to restart each app the table lists as wired.

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

It looks for each agent's configuration folder, registers the inspeximus memory server in every agent it
finds, and points all of them at one store, `~/.inspeximus/coding_memory.json`. If your agents already
point at one existing store, that store is kept. If they point at different stores, it stops and asks you
to choose one with `--store <path>`. It prints one table:

```text
host         found  wired   store path                                   recall
Claude Code  yes    add     /home/you/.inspeximus/coding_memory.json     hooks
Codex CLI    yes    create  /home/you/.inspeximus/coding_memory.json     instructions
...
```

It never replaces your other settings in an agent's configuration, keeps a `.bak` copy of every file it
changes, and records one decision in the shared memory, so the next session in any agent can confirm the
memory works. If the current project had an older Claude Code memory in `.inspeximus/coding_memory.json`,
its records are imported into the shared store.

## Per agent

<a id="claude-code"></a>
### Claude Code

Configuration: `~/.claude.json`, plus hooks in `~/.claude/settings.json`. The SessionStart hook recalls the
shared memory at the start of every session. Restart Claude Code. Do not also install the marketplace
plugin, or every hook runs twice.

<a id="codex"></a>
### Codex CLI

Configuration: `~/.codex/config.toml`, table `[mcp_servers.inspeximus]`. Codex shows the server's
instructions to the model with its tools. Restart Codex.

<a id="gemini-cli"></a>
### Gemini CLI

Configuration: `~/.gemini/settings.json`, key `mcpServers`. Gemini CLI appends the server's instructions to
its system instructions, so it is told to recall at the start of each task. Restart Gemini CLI.

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

<a id="windsurf"></a>
### Windsurf

Windsurf is now Devin Desktop. Its agents read the Devin configuration described in the next section, and
the installer writes it. For older Windsurf builds, it also writes `~/.codeium/windsurf/mcp_config.json`.
With `--rules yes`, the recall rule is appended to `~/.codeium/windsurf/memories/global_rules.md` as well.
Restart Windsurf.

<a id="devin"></a>
### Devin Desktop and Devin CLI

Configuration: `~/.config/devin/mcp_config.json` on Linux and macOS (`$XDG_CONFIG_HOME/devin` when that is
set), `%APPDATA%\devin\mcp_config.json` on Windows. Devin Desktop and Devin CLI share this file. With
`--rules yes`, the recall rule is appended to `AGENTS.md` in the same folder, which Devin loads at the start
of every session. Restart Devin Desktop. Versions before v3000.3 read servers from `config.json` instead;
update Devin, which moves them on startup.

<a id="cline"></a>
### Cline

Configuration: `~/.cline/data/settings/cline_mcp_settings.json`. With `--rules yes`, the recall rule goes to
`~/Documents/Cline/Rules/inspeximus.md`. Cline reloads its configuration by itself.

<a id="hermes-agent"></a>
### Hermes Agent

When the installer finds Hermes' own virtual environment (under `$HERMES_HOME`, `~/.hermes`, or
`%LOCALAPPDATA%\hermes`), it installs inspeximus into it, sets `memory.provider: inspeximus` in
`config.yaml`, and points the provider at the shared store. If Hermes already uses another memory provider,
that provider is kept unless you pass `--hermes-provider yes`; the default is `no`. Restart Hermes.

## Check it

In any agent, ask: "What do you remember about the inspeximus setup?" The agent finds the decision the
installer recorded, which names every agent that was wired.

## Sources

Each configuration path and rules location on this page comes from the host's own documentation, read on
2026-09-26.

| Agent | What the source gives | Source |
|---|---|---|
| Claude Code | `~/.claude.json` for MCP servers | https://code.claude.com/docs/en/mcp |
| Codex CLI | `~/.codex/config.toml`, `[mcp_servers.<name>]` | https://learn.chatgpt.com/docs/extend/mcp |
| Codex CLI | server instructions go into the tool description the model sees | https://github.com/openai/codex/blob/75e0e0aad97a86138b8b1ec87d9b544b4a35ecbf/codex-rs/codex-mcp/src/rmcp_client.rs |
| Gemini CLI | `~/.gemini/settings.json`, `mcpServers`; instructions appended to the system instructions | https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md |
| Antigravity | `~/.gemini/config/mcp_config.json` | https://antigravity.google/docs/mcp |
| Antigravity | `~/.gemini/config/rules/*.md`, `trigger: always_on` | https://antigravity.google/docs/rules |
| Cursor | `~/.cursor/mcp.json` | https://cursor.com/docs/mcp |
| Cursor | `.cursor/rules/*.mdc` with `alwaysApply`; global rules in settings only | https://cursor.com/docs/rules |
| Windsurf | Cascade reads the Devin file; `~/.codeium/windsurf/mcp_config.json` is the editor's discovery file | https://docs.devin.ai/windsurf/plugins/cascade/mcp |
| Windsurf | `~/.codeium/windsurf/memories/global_rules.md` | https://docs.devin.ai/desktop/cascade/memories |
| Devin Desktop, Devin CLI | `~/.config/devin/mcp_config.json`, `%APPDATA%\devin\mcp_config.json`; v3000.3 change | https://docs.devin.ai/cli/extensibility/mcp/configuration |
| Devin Desktop, Devin CLI | global rules in `AGENTS.md` in the same folder | https://docs.devin.ai/cli/extensibility/rules |
| Cline | `~/.cline/data/settings/cline_mcp_settings.json` | https://docs.cline.bot/getting-started/config |
| Cline | `~/Documents/Cline/Rules` | https://docs.cline.bot/customization/cline-rules |
| Hermes Agent | `memory.provider` in `~/.hermes/config.yaml` | https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers |
| Hermes Agent | Hermes' own virtual environment | https://hermes-agent.nousresearch.com/docs/getting-started/installation |
