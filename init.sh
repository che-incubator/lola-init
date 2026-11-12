#!/bin/sh
# lola-init: delivers AI agent configuration to a Dev Spaces workspace.
#
# Runs as an init container. Clones a config repo in the agent-plugin format and installs it into the
# user's home with Lola. It never fails the workspace start because of a script failure: every failure
# path exits 0, and the result is written to ~/.lola/init-status.
#
# Config repo, first match wins:
#   LOLA_REPO_URL (+ LOLA_REPO_REF)                personal: the user's own Secret
#   LOLA_DEFAULT_REPO_URL (+ LOLA_DEFAULT_REPO_REF) default: the admin's ConfigMap
# Neither set: do nothing. The ref always comes from the same pair as the URL.
#
# Opt-out: LOLA_DISABLED=true (exactly "true") turns the init off. It is checked first, so it wins over
# both pairs. Nothing is fetched or changed and config that is already installed stays in place; only the
# status file is written. Unset the variable and the next start picks up from the recorded source.
#
# Agents: LOLA_AGENT picks the Lola targets. One name (claude-code, the default), a comma-separated
# subset (claude-code,opencode), or all (every target Lola supports).
#
# Start modes (decided from the source recorded after the last successful run):
#   install  nothing installed yet
#   update   same source: lola mod update + lola update
#   switch   different source: remove the old module(s), then install the new one
MARK="[lola-init]"
STATE="$HOME/.lola"
STATUS="$STATE/init-status"
RECORD="$STATE/init-source"
LOG="$(mktemp 2>/dev/null)" || LOG=/dev/null
trap '[ "$LOG" = /dev/null ] || rm -f "$LOG"' EXIT
ORIGIN=none

# Hide credentials embedded in URLs (https://user:token@host/...) in anything we print or store.
redact() { sed -E 's#(://)[^/@[:space:]]*@#\1***@#g'; }
say() { echo "$MARK $*" | redact; }
write_status() { mkdir -p "$STATE" 2>/dev/null; printf '%s\n' "$1" | redact > "$STATUS" 2>/dev/null; return 0; }
fail() {
  say "FAILED: $1"
  tail -n 15 "$LOG" 2>/dev/null | redact | sed "s/^/$MARK   /"
  write_status "failed $(date -u +%FT%TZ) origin=$ORIGIN reason=$1"
  exit 0
}
lcmd() { timeout "${LOLA_TIMEOUT:-120}" "$@" >> "$LOG" 2>&1; }
modules() { ls -1 "$STATE/modules" 2>/dev/null; }

cd "$HOME" 2>/dev/null || { say "FAILED: cannot cd to HOME"; exit 0; }

if [ "${LOLA_DISABLED:-}" = true ]; then
  say "disabled by LOLA_DISABLED=true; installed config is left untouched, nothing fetched"
  write_status "ok $(date -u +%FT%TZ) disabled"
  exit 0
fi
[ -z "${LOLA_DISABLED:-}" ] || say "LOLA_DISABLED=$LOLA_DISABLED ignored: only the value true disables the init"

if [ -n "${LOLA_REPO_URL:-}" ]; then
  ORIGIN=personal; URL=$LOLA_REPO_URL; REF=${LOLA_REPO_REF:-}
elif [ -n "${LOLA_DEFAULT_REPO_URL:-}" ]; then
  ORIGIN=default; URL=$LOLA_DEFAULT_REPO_URL; REF=${LOLA_DEFAULT_REPO_REF:-}
else
  say "no config repo set (LOLA_REPO_URL / LOLA_DEFAULT_REPO_URL); nothing to do"
  exit 0
fi

AGENTS=$(printf '%s' "${LOLA_AGENT:-claude-code}" | tr ',' ' ')
for a in $AGENTS; do
  case "$a" in
    all) AGENTS=all; break ;;
    claude-code|copilot-cli|copilot-vscode|cursor|gemini-cli|openclaw|opencode) ;;
    *) fail "unsupported LOLA_AGENT=$a (supported: claude-code, opencode, gemini-cli, cursor, copilot-vscode, copilot-cli, openclaw, all)" ;;
  esac
done
# Canonical form: comma-separated, sorted, no duplicates. It is what the record compares on.
AGENTS=$(printf '%s\n' $AGENTS | sort -u | tr '\n' ',' | sed 's/,$//')
export GIT_TERMINAL_PROMPT=0
SRC_HASH=$(printf '%s %s' "$URL" "$REF" | sha256sum | cut -d' ' -f1)

existing=$(modules)
mode=install
if [ -n "$existing" ]; then
  if [ -f "$RECORD" ]; then
    # Line 1 is the source hash; field 2 is the agent list (absent in homes written before it was recorded).
    read -r recorded rec_agents < "$RECORD" 2>/dev/null
    [ -n "${rec_agents:-}" ] || rec_agents=claude-code
    if [ "$recorded" = "$SRC_HASH" ] && [ "$rec_agents" = "$AGENTS" ]; then mode=update; else mode=switch; fi
  else
    # Home written by an earlier version without a record: compare the source Lola stored.
    count=$(printf '%s\n' "$existing" | wc -l | tr -d ' ')
    first=$(printf '%s\n' "$existing" | head -1)
    stored=$(sed -n 's/^source: //p' "$STATE/modules/$first/.lola/source.yml" 2>/dev/null)
    if [ "$count" = 1 ] && [ "$stored" = "$URL" ]; then mode=update; else mode=switch; fi
  fi
fi

start_mode=$mode
if [ "$mode" = switch ]; then
  say "config source changed (now: $ORIGIN agent=$AGENTS); replacing the installed module"
  # Check the new source first, so an unreachable one does not wipe a working install.
  timeout 30 git ls-remote "$URL" >> "$LOG" 2>&1 || fail "new config source is not reachable; kept the existing install"
  for m in $existing; do
    # "lola mod rm" alone leaves the installed files, instructions block and MCP entries in place. No "-a":
    # the module is ours, so remove it from every assistant it was installed for, including a changed set.
    lcmd lola uninstall "$m" -s user -f
    lcmd lola mod rm "$m" -f || fail "could not remove old module $m"
  done
  mode=install
fi

if [ "$mode" = update ]; then
  module=$(printf '%s\n' "$existing" | head -1)
  lcmd lola mod update "$module" || fail "lola mod update"
  # No "-a": Lola regenerates the files of every assistant the module is installed for.
  lcmd lola update "$module" || fail "lola update"
else
  lcmd lola mod add "$URL" ${REF:+--ref "$REF"} || fail "lola mod add"
  module=$(modules | head -1)
  [ -n "$module" ] || fail "no module registered after lola mod add"
  rc=0
  if [ "$AGENTS" = all ]; then
    # No "-a": Lola installs for every target it supports.
    lcmd lola install "$module" -s user -f || rc=1
  else
    for a in $(printf '%s' "$AGENTS" | tr ',' ' '); do
      lcmd lola install "$module" -a "$a" -s user -f || rc=1
    done
  fi
  if [ "$rc" != 0 ]; then
    lcmd lola uninstall "$module" -s user -f   # so the next start retries from scratch
    lcmd lola mod rm "$module" -f
    fail "lola install"
  fi
fi

# Lola prints module warnings only during "lola mod add". Ask its loader on every start instead, so an
# invalid entry (for example an unsupported MCP type) is reported rather than silently dropped.
wlist=$(python3 /opt/lola_warnings.py "$module" 2>>"$LOG")
warnings=0
if [ -n "$wlist" ]; then
  warnings=$(printf '%s\n' "$wlist" | wc -l | tr -d ' ')
  printf '%s\n' "$wlist" | head -10 | redact | sed "s/^/$MARK lola: /"
fi

# Claude Code reads user-scope MCP from ~/.claude.json; Lola writes OpenCode's MCP itself.
case ",$AGENTS," in
  *,claude-code,*|*,all,*)
    python3 /opt/adapt_mcp.py "$HOME" >> "$LOG" 2>&1; rc=$?
    grep "^\\$MARK" "$LOG" | tail -3 | redact
    [ "$rc" = 0 ] || fail "MCP sync"
    ;;
esac

printf '%s %s\n' "$SRC_HASH" "$AGENTS" > "$RECORD"
write_status "ok $(date -u +%FT%TZ) $module origin=$ORIGIN mode=$start_mode warnings=$warnings agent=$AGENTS url=$URL"
say "ok module=$module origin=$ORIGIN mode=$start_mode warnings=$warnings agent=$AGENTS"
