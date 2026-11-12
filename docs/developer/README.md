# Developer guide: your own agent config in every workspace

`lola-init` installs your skills, agents, instructions and MCP servers at every workspace start: into
`~/.claude` and `~/.claude.json` for Claude Code, into `~/.config/opencode` for OpenCode, and into the native
location of any other Lola-supported assistant you select. If your admin set
a recommended repo you already get that; the steps below replace it with yours. See the
[admin guide](../admin/README.md) for how it is enabled.

## 1. Make a config repo

Use the [agent-plugin](https://agent-plugins.org) layout:

```
plugin.json
skills/<name>/SKILL.md
dev.getlola/agents/<name>.md
dev.getlola/AGENTS.md
mcp.json
```

`plugin.json`:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "my-agent-config",
  "version": "0.1.0",
  "description": "My agent configuration.",
  "extensions": {
    "dev.getlola": {
      "agents": "./dev.getlola/agents",
      "instructions": "./dev.getlola/AGENTS.md"
    }
  }
}
```

`mcp.json` (optional):

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "my-local-server": {"type": "stdio", "command": "npx", "args": ["--package", "some-package@1.2.3", "some-bin"]},
    "my-remote-server": {"type": "streamable-http", "url": "https://example.com/mcp"}
  }
}
```

Rules that cost real debugging time:

- **No secrets.** Everything in the repo, including `env` values in `mcp.json`, ends up in git and in `~/.claude.json`.
- **Remote servers use `type: streamable-http`** (or `sse`). `http` is not an agent-plugin type: Lola drops the entry.
  The init now logs it as a warning (see "Check it worked").
- **stdio servers:** `command` is one bare executable name (no spaces, no path). `cwd` is accepted by Lola but is
  not copied into `~/.claude.json`.
- **Use distinctive names.** Lola overwrites a same-named skill or agent without asking. The init also manages the
  MCP server names it adds: do not reuse the name of a server you added by hand.
- **Do not edit inside the Lola block** of `~/.claude/CLAUDE.md` (it holds `dev.getlola/AGENTS.md`). Edits there are lost on the next update.
- **Not carried:** `settings.json` (hooks, permissions, env). Keep paths portable (no `/Users/...`).

## 2. Point your workspaces at it

Create this Secret in your namespace, then restart the workspace:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: lola-config
  labels:
    controller.devfile.io/mount-to-devworkspace: "true"
    controller.devfile.io/watch-secret: "true"
  annotations:
    controller.devfile.io/mount-as: env
stringData:
  # Your config repo: a git URL.
  LOLA_REPO_URL: https://github.com/<you>/<config-repo>.git
  # The git ref to check out: a branch name, a tag or a commit SHA (for example main or v1.2.0).
  # Optional: leave it out to use the repo's default branch.
  LOLA_REPO_REF: main
  # Optional: the assistants to install for. One name (claude-code, the default), a comma-separated
  # subset (claude-code,opencode,gemini-cli), or all.
  LOLA_AGENTS: opencode
```

The Secret mounts into **every workspace in the namespace**. To limit it, add the annotation
`controller.devfile.io/mount-to-devworkspace-include: name1,prefix-*` (and `-exclude` to leave some out).
A private repo works through the git credentials your workspace already has; a public one needs none.
`LOLA_TIMEOUT` sets the timeout in seconds for each Lola command, 120 by default. It goes into the same
`Secret` or into the administrator's `ConfigMap`.

## 3. Check it worked

- `cat ~/.lola/init-status` should read
  `ok <time> <module> origin=personal mode=install warnings=0 agents=<list> url=<url>`. `mode` is `install` on a
  fresh home, `update` when the repository and the assistant set did not change, and `switch` after a change of
  either.
- Claude Code: `ls ~/.claude/skills ~/.claude/agents` lists your items, and `claude mcp list` lists your
  servers (a remote server may ask you to log in). OpenCode: `ls ~/.config/opencode/skills
  ~/.config/opencode/agents`, and the servers are under `mcp` in `~/.config/opencode/opencode.json`.
- The init log has the details: `oc logs <workspace pod> -c lola-init`.
- `warnings=N` with `N` above 0 means Lola dropped entries. The log lines that start with `[lola-init] lola:`
  name them, for example `mcp.json: server 'x': unsupported type`.

## 4. Change or remove it

- **Update:** push to your repo and restart the workspace. New commits arrive, and skills you deleted from the repo
  are removed.
- **Another branch or tag:** change `LOLA_REPO_REF`.
- **Another repo:** change `LOLA_REPO_URL`. The init removes the old config completely, then installs the new one.
- **Another or more assistants:** change `LOLA_AGENTS` (`claude-code`, `opencode`, a comma-separated list,
  or `all`). Same switch path: the config is removed from every assistant of the old set, then installed for the
  new set. An old assistant's instructions file can stay behind, empty.
- **Back to the recommended repo:** delete the Secret and restart the workspace.
- **Turn it off, no config at all:** add `LOLA_DISABLED: "true"` to the same Secret (the exact value `true`; the
  Secret needs the keys above only if you also want a personal repo) and restart the workspace. It wins over your repo
  and the recommended one: the init fetches nothing and the status file reads `ok <time> disabled`. Config that is
  already installed is **not removed**; delete `~/.claude/skills/<name>`, `~/.claude/agents/<name>.md` and the Lola
  block in `~/.claude/CLAUDE.md` yourself if you want it gone (OpenCode: the same under `~/.config/opencode`).
  A new workspace starts with none. Remove the key to turn it back on.
- **No recommended repo exists:** with no URL set the init does nothing, and files already installed stay.

## When something is wrong

| You see | Meaning | Do |
|---|---|---|
| `failed … reason=lola mod add`, log has `could not read Username` | the repo is private and the workspace has no git credentials | make the repo public, or add git credentials |
| `new config source is not reachable; kept the existing install` | wrong URL or no access; your old config is still there | fix the URL or access, restart |
| `warnings=N` with `N` above 0 | Lola dropped entries | read the `[lola-init] lola:` lines |
| `ok <time> disabled`, nothing new installed | `LOLA_DISABLED=true` reached the workspace | remove the key from the Secret if you want the config back |
| no status file, nothing installed | no URL reached the workspace, or the admin did not enable the init container | check the Secret labels, then ask your admin |

A failing init never stops the workspace from starting. A broken init image does, and so does a cluster with
`persistUserHome` off (the init container mounts `persistent-home`, which then does not exist): that is an admin
matter (see the admin guide).
