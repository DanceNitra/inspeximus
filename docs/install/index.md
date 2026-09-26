# Install inspeximus: one memory for all your AI agents

This page is written so an AI agent can follow it for you. It installs inspeximus once and connects every
AI agent on this computer to the same memory: Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor,
Windsurf, Cline, and Hermes Agent. A decision recorded in one agent is recalled in the others.

## For an agent following this page

Run the commands for your operating system exactly as written. Before the last command, ask the user one
question: "Some of your agents don't read instructions from a memory server. Can I add one line to their
rules files telling them to check the shared memory at the start of each task?" Pass the answer as
`--rules yes` or `--rules no`. When you finish, tell the user to restart each app the table lists as wired.

<a id="linux-macos"></a>
## Linux and macOS

Requires Python 3.9 or later.

<!-- ci: posix -->
```bash
python3 -m venv ~/.inspeximus/venv
~/.inspeximus/venv/bin/python -m pip install "inspeximus[mcp]"
~/.inspeximus/venv/bin/inspeximus install --all --rules yes
```

<a id="windows"></a>
## Windows (PowerShell)

Requires Python 3.9 or later.

<!-- ci: windows -->
```powershell
python -m venv "$HOME\.inspeximus\venv"
& "$HOME\.inspeximus\venv\Scripts\python.exe" -m pip install "inspeximus[mcp]"
& "$HOME\.inspeximus\venv\Scripts\inspeximus.exe" install --all --rules yes
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
the rule goes to the current project's `.cursor/rules/inspeximus.mdc`; outside a project, the installer
prints the line to paste into Customize > Rules. Restart Cursor.

<a id="windsurf"></a>
### Windsurf

Configuration: `~/.codeium/windsurf/mcp_config.json`. With `--rules yes`, the recall rule is appended to
`~/.codeium/windsurf/memories/global_rules.md`. Restart Windsurf.

<a id="cline"></a>
### Cline

Configuration: `~/.cline/data/settings/cline_mcp_settings.json`. With `--rules yes`, the recall rule goes to
`~/Documents/Cline/Rules/inspeximus.md`. Cline reloads its configuration by itself.

<a id="hermes-agent"></a>
### Hermes Agent

When the installer finds Hermes' own virtual environment (under `$HERMES_HOME`, `~/.hermes`, or
`%LOCALAPPDATA%\hermes`), it installs inspeximus into it, sets `memory.provider: inspeximus` in
`config.yaml`, and points the provider at the shared store. If Hermes already uses another memory provider,
it asks first; pass `--hermes-provider yes` or `--hermes-provider no` to answer in advance. Restart Hermes.

## Check it

In any agent, ask it to recall `inspeximus-setup`. It finds the decision the installer recorded, which
names every agent that was wired.
