# A store reached through a link

A repository can ship a link at `.inspeximus`, or at the store file inside it. Followed without a check, the link makes another
project's memory reach the hook output and the model, and lets a session write into that store.

## When a link is followed

inspeximus follows a link, a junction included, only when one of these holds:

1. **Your config names the target.** `inspeximus link <directory-or-file>` records it in `<key home>/inspeximus/config.json` under
   `stores.links`. The shared store that `inspeximus install --all` records needs no entry.
2. **The target stays inside the project**, or inside the main checkout when the project is a git worktree, and never inside `.git`.
3. **Git does not track the link.** A `git clone` can only deliver a tracked link.

`INSPEXIMUS_CODING_STORE` is judged by conditions 1 and 2, because a project's Claude Code settings can set it.
`INSPEXIMUS_PATH` is judged only when it names a link that lies inside the project; a plain path, or a link outside the project,
is yours and is used as it is. `INSPEXIMUS_SCOPE=project` and `claude-code` follow the same rules as a shipped link.

When none holds, there is no store: the hooks print one line on stderr and nothing else, and the MCP server answers each call
with the refusal. Nothing is read or written, and the default location is not used instead.

## Allowing a store

Run the command yourself, in a terminal:

```
inspeximus link <directory-or-file>
```

It asks you to confirm. With no terminal it refuses unless you pass `--yes`, so an agent that runs it must add a flag that you see
in the tool prompt. Check that the store is yours before you allow it: the path in the refusal was chosen by the repository.

## A known gap

A directory that arrives with its own `.git`, for example in a tar archive, can make a shipped link look untracked, so condition 3
allows it. Conditions 1 and 2 do not depend on git.
