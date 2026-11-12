# lola-init

Init container that delivers AI agent configuration (skills, agents, instructions, MCP servers) to a Dev
Spaces workspace, for whichever AI assistants you select: one, several, or all of those that Lola supports.
It clones a config repo in the
[agent-plugin](https://agent-plugins.org) layout and installs it into the home directory with
[Lola](https://github.com/LobsterTrap/lola) (pinned commit, since agent-plugin support is unreleased).

## Which repo it installs

| Variable | Set by | Meaning |
|---|---|---|
| `LOLA_REPO_URL`, `LOLA_REPO_REF` | the user (Secret in their namespace) | personal config repo |
| `LOLA_DEFAULT_REPO_URL`, `LOLA_DEFAULT_REPO_REF` | the admin (ConfigMap synced to every namespace) | recommended default |
| `LOLA_DISABLED` | the user (same Secret) | `true` turns the init off |
| `LOLA_AGENTS` | the admin (ConfigMap) or the user (same Secret) | Lola targets: `claude-code` (default), a comma-separated subset such as `claude-code,opencode`, or `all` |

The personal pair wins when set, otherwise the default pair is used. The ref always comes from the pair
the URL came from. Nothing set: the init does nothing.

The agent set is part of the source: changing `LOLA_AGENTS` takes the switch path, which removes the config
installed for the previous set and installs it for the new one. Order and duplicates do not matter
(`opencode,claude-code` is the same set as `claude-code,opencode`). An unknown name is refused with a `failed`
status. `all` means every target Lola supports, and the container passes it to Lola as "no `-a`" so a new Lola
target needs no change here.

`LOLA_DISABLED=true` is the opt-out. It is checked first, so it wins over both pairs: the init fetches
nothing, changes nothing and writes only the status file (`ok <time> disabled`). Config that is already
installed stays in place (it is not uninstalled), and a new workspace home gets none. Remove the variable and the
next start continues from the recorded source (update path, or switch if the source changed). Only the exact
value `true` counts; any other non-empty value is ignored and the init logs that.

Each pair is a repo and a ref:

- `…_REPO_URL`: the git URL of the config repo, for example `https://github.com/<org>/<repo>.git`.
- `…_REPO_REF`: the git ref to check out. A branch name, a tag or a commit SHA, for example `main` or `v1.2.0`.
  Optional: with no ref the init clones the repo's default branch.
- The ref is a separate variable because Lola takes it as `--ref`. Changing only the ref (another branch or tag)
  takes the same switch path as changing the URL.

## What it does on each start

- **install**: nothing installed yet. `lola mod add`, then `lola install -a <agent> -s user` once per agent
  (`all`: `lola install -s user`, which covers every target).
- **update**: same source and agent set as last time. `lola mod update`, `lola update` (new commits arrive,
  removed skills go; Lola regenerates the files of every assistant the module is installed for).
- **switch**: different source or agent set. Checks the new source is reachable, then `lola uninstall` without
  `-a` (the module is ours, so it goes from every assistant it was installed for) + `lola mod rm`, then installs.

Lola writes user-scope MCP servers to `~/.mcp.json`; Claude Code reads `~/.claude.json`. `adapt_mcp.py`
syncs them and remembers which names it added, so a removed or switched-away server is removed again.
OpenCode needs no adapter: Lola writes its MCP servers straight into `~/.config/opencode/opencode.json`.
`lola_warnings.py` reports entries Lola dropped (Lola itself prints these only during `mod add`).

It never fails the workspace start because of a script failure: every failure path exits 0. The result is in
`~/.lola/init-status` (`ok|failed <time> <module> origin=… mode=… warnings=… agents=… url=…`, or
`ok <time> disabled` when opted out). URLs are printed and stored without embedded credentials.

## Limits

- Codex is not supported: Lola has no `codex` target (LobsterTrap/lola#132). `settings.json` (hooks,
  permissions, env) is not carried. Gemini CLI has no agents, and Lola still crashes on a command whose front
  matter `description` is a list, so keep command descriptions a single string.
- Switching the agent set removes the module from each assistant it was installed for, but an instruction file
  can stay behind empty (`~/.claude/CLAUDE.md`, `~/.config/opencode/AGENTS.md`), plus any MCP entries the Claude
  adapter added to `~/.claude.json` (that adapter runs only when `claude-code` or `all` is selected).
- Reusing the name of a managed MCP server for one you added by hand lets the adapter overwrite and later remove it.
- Removing or replacing the installed module touches only the module lola-init installed. Modules you add to
  `~/.lola/modules` yourself stay, and the init refuses to switch rather than guess when it cannot tell them apart.
- A container that cannot start at all (image not pullable, wrong architecture) does block the workspace pod.
  Publish multi-arch (amd64 + arm64) images and pin them by digest.

## Build and test

```
docker buildx build --platform linux/amd64 --load -t lola-init:dev-amd64 .
IMG=lola-init:dev-amd64 PLATFORM=linux/amd64 tests/run-tests.sh
# optional: V1_IMG=<image of an earlier version> adds the upgrade test (amd64 only)
```

Pushes to `main` publish `quay.io/che-incubator/lola-init:latest` and `:sha-<commit>` for amd64 and arm64
(the `QUAY_USERNAME` and `QUAY_PASSWORD` repository secrets hold the robot account). The index digest is in
the workflow run summary.

The tests need Docker and git. They build local fixture repos, run the image against them as uid 1000 and
check the files in the home volume (install, restart, new commit, non-default ref, personal over default and
back, unreachable repo, credentials in URLs, invalid MCP entries, items removed from the repo, opt-out,
switching between one, several and all assistants).

The two Python helpers (`adapt_mcp.py`, `lola_warnings.py`) also have unit tests that need no Docker and no
dependencies (standard library only):

```
python3 -m unittest discover -s tests/unit -v
```

GitHub Actions runs both layers on every pull request and on pushes to `main` (`.github/workflows/ci.yml`).
