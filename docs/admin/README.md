# Admin guide: enable lola-init for all workspaces

`lola-init` installs an AI agent configuration repo (Claude Code skills, agents, instructions, MCP
servers) into every workspace home at start. The admin sets a recommended repo; a developer can replace it
with their own. See the [repo README](../../README.md) for how the init behaves.

Two objects enable it for a whole cluster, with no che-operator or DWO code change:

| File | What it is | Where it goes |
|---|---|---|
| [`checluster-patch.yaml`](checluster-patch.yaml) | the init container, added to `devEnvironments.initContainers` | the CheCluster |
| [`workspaces-config-configmap.yaml`](workspaces-config-configmap.yaml) | the recommended repo URL and ref | the Che namespace, copied to every user namespace |

## Prerequisites

- **`persistUserHome` must be enabled** (the Che default). The `lola-init` entry mounts `persistent-home`, a
  volume DWO defines only when `persistUserHome` is enabled. With it off, every workspace start fails at once with
  `container `lola-init` references undefined volume 'persistent-home'`. Do not add the entry on a
  cluster that turns `persistUserHome` off.
- The image is public and multi-arch (amd64 and arm64), pullable from every user namespace. Pin it by digest:
  `docker buildx imagetools inspect quay.io/che-incubator/lola-init:latest` prints the index digest for the
  CheCluster entry.
- Verified with Eclipse Che 7.124 and DWO 1.0. The arm64 image is tested in Docker; it has not run on an arm64
  cluster node yet.
- The recommended repo is readable by the init: public, or the workspaces have git credentials (the init uses the
  git credentials DWO mounts). On a cluster without user git credentials a private default repo cannot be
  cloned: the init reports the failure and the workspace still starts.
- Namespaces that Che provisions already carry the `che.eclipse.org/username` annotation that the ConfigMap sync
  needs. A namespace created by hand needs it too, or the sync skips it (log line: "Username is not set for the
  namespace. Synchronization skipped.").

## Enable

1. Create the ConfigMap in the Che namespace (edit the URL and ref first):
   `oc apply -n <che-namespace> -f workspaces-config-configmap.yaml`
2. Add the init container to the CheCluster. Look at the current list first:
   `oc get checluster <name> -n <che-namespace> -o jsonpath='{.spec.devEnvironments.initContainers}'`
   - Empty or absent: `oc patch checluster <name> -n <che-namespace> --type merge --patch-file checluster-patch.yaml`
   - Already has entries: a merge patch would replace the whole list. Append instead:
     `oc patch checluster <name> -n <che-namespace> --type json -p '[{"op":"add","path":"/spec/devEnvironments/initContainers/-","value":{...the entry from checluster-patch.yaml as JSON...}}]'`
3. Start a new workspace and verify (next section).

Creating or changing the ConfigMap restarts the running workspaces that receive the synced copy, which is every
user namespace. Apply it in a quiet window; the restart load at scale has not been measured. A ConfigMap scoped
with the annotation `controller.devfile.io/mount-to-devworkspace-include` (copied into every namespace) does not
touch workspaces outside that scope; it is a way to try the setup on a few workspaces first.

The CheCluster entry reaches the Che-managed DWOC (`devworkspace-config` in the Che namespace) within seconds.
Running workspaces keep their pod; workspaces started afterwards get the init container.

## Verify

For a new workspace `<name>` in a user namespace:

```
oc get pod -l controller.devfile.io/devworkspace_name=<name> -o jsonpath='{.items[0].spec.initContainers[*].name}'
oc logs -l controller.devfile.io/devworkspace_name=<name> -c lola-init
```

Expected log lines: `[lola-init] ok module=<name> origin=default mode=install warnings=0 agents=<list>`. Inside
the workspace, `~/.lola/init-status` holds `ok <time> <module> origin=… mode=… warnings=… agents=… url=…`, with
the full URL and the same values. The content lands in the directories of each selected assistant: for Claude
Code, skills in `~/.claude/skills`, agents in `~/.claude/agents`, MCP servers in `~/.claude.json`.

## Operate

- **Change the recommended repo or ref:** edit the ConfigMap. The synced copies update within seconds; running
  workspaces keep the old value until they restart. A changed URL or ref takes the switch path on the next start
  (old module removed, new one installed).
- **Change the image:** build and push a new tag, read its index digest from the registry
  (`docker buildx imagetools inspect <image>:<tag>`), and replace the digest in the CheCluster entry.
- **Roll back:** set `initContainers` back to its previous value (`--type merge` with `null` removes the key), and
  delete the ConfigMap so the sync removes the copies. Files already installed in workspaces stay.

## Developer override

A developer replaces the recommended repo by creating this Secret in their namespace (the personal pair wins over
the default pair; the ref comes from the pair the URL came from):

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
  # The developer's config repo: a git URL.
  LOLA_REPO_URL: https://github.com/<user>/<config-repo>.git
  # The git ref to check out: a branch name, a tag or a commit SHA (for example main or v1.2.0).
  # Optional: leave it out to use the repo's default branch.
  LOLA_REPO_REF: main
```

Deleting the Secret and restarting the workspace switches back to the recommended repo. Private repos work
through the git credentials the workspace already has.

## Failure behavior

- A script failure never blocks the workspace start. It is logged and written to the status file
  (`failed <time> origin=… reason=…`). Example: a default repo that cannot be cloned. The only exception
  is a home the script cannot enter: it exits 0 without a status file, and the container log says so.
- A container that cannot start at all fails the workspace start quickly and with a clear message. An init image
  that cannot be pulled ends in `Failed` with `Init Container lola-init has state ImagePullBackOff`, and no pod is
  left behind.
- `persistUserHome` off fails every start at once: `lola-init` mounts `persistent-home`, which then does not
  exist (see Prerequisites).
- An unreachable new repo during a switch leaves the existing install in place.
- Credentials embedded in a URL are not printed or stored by the init: its log and the status file are
  redacted, and `~/.lola/init-source` holds a hash. Lola and git still receive the URL as given.
- A hand-made workspace must carry the attributes Che sets, for example the SCC attribute for the cluster's
  container-build setting. A workspace without it fails on the security context constraint regardless of this
  init container.

## Limits

- A developer can opt out with `LOLA_DISABLED=true` in their Secret (see the developer guide, section 4). It
  stops the init from fetching or changing anything, but does not uninstall config that is already in the workspace
  home; a new workspace gets none. An admin cannot force the init on for a developer who sets it.
- The MCP adapter is Claude Code only. Claude Code `settings.json` (hooks, permissions, env) is not carried.
- Users cannot edit the synced ConfigMap copy: the controller reverts edits.
