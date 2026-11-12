#!/usr/bin/env python3
"""Sync MCP servers from ~/.mcp.json (written by Lola) into ~/.claude.json (read by Claude Code).

Lola writes user-scope MCP servers to ~/.mcp.json; Claude Code reads them from ~/.claude.json.
The adapter keeps a list of the server names it manages (~/.lola/init-managed-mcp.json),
so a server removed from the config repo, or dropped by a source switch, is removed again.
Servers the user added by hand are never touched, unless they reuse a managed name.

~/.claude.json is rewritten only if it parses; every other key is kept; mode 0600; atomic replace.
"""
import json
import os
import sys
import tempfile

MARK = "[lola-init]"
KEEP = ("type", "command", "args", "env", "url", "headers")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def convert(entry):
    out = {k: entry[k] for k in KEEP if k in entry}
    if "type" not in out and "command" in out:
        out["type"] = "stdio"
    return out


def write_atomic(path, data, mode):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(home):
    src = os.path.join(home, ".mcp.json")
    dst = os.path.join(home, ".claude.json")
    managed_path = os.path.join(home, ".lola", "init-managed-mcp.json")
    try:
        servers = (load(src, {}) or {}).get("mcpServers", {}) or {}
        prev = (load(managed_path, {}) or {}).get("names", []) or []
    except (ValueError, OSError, AttributeError) as e:
        print("%s FAILED reading MCP state: %s" % (MARK, e), file=sys.stderr)
        return 1
    if not isinstance(servers, dict) or not isinstance(prev, list):
        print("%s FAILED: unexpected shape in %s or %s" % (MARK, src, managed_path), file=sys.stderr)
        return 1
    if not servers and not prev:
        print("%s no MCP servers to sync" % MARK)
        return 0
    try:
        cfg = load(dst, {})
    except (ValueError, OSError) as e:
        print("%s FAILED reading %s: %s (left untouched)" % (MARK, dst, e), file=sys.stderr)
        return 1
    if not isinstance(cfg, dict) or not isinstance(cfg.get("mcpServers", {}), dict):
        print("%s FAILED: unexpected shape in %s (left untouched)" % (MARK, dst), file=sys.stderr)
        return 1

    cur = cfg.setdefault("mcpServers", {})
    removed = [n for n in prev if n not in servers and n in cur]
    for n in removed:
        del cur[n]
    changed = len(removed)
    added = 0
    for name, entry in servers.items():
        new = convert(entry)
        if cur.get(name) != new:
            cur[name] = new
            added += 1
    changed += added

    if changed:
        write_atomic(dst, cfg, 0o600)
    names = sorted(servers)
    if names != sorted(prev):
        os.makedirs(os.path.dirname(managed_path), exist_ok=True)
        write_atomic(managed_path, {"names": names}, 0o600)
    print("%s MCP sync: %d server(s) set, %d added or updated, %d removed" % (MARK, len(servers), added, len(removed)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~")))
