"""Unit tests for adapt_mcp.py: sync MCP servers from ~/.mcp.json into ~/.claude.json.

No Docker needed. Run from the repo root:

    python3 -m unittest discover -s tests/unit -v

Every guard has a positive twin: invalid input must leave ~/.claude.json untouched (FailureTest), and valid
input must still be written (SyncTest). Without the twin a test would also pass if the guard were missing.
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import adapt_mcp  # noqa: E402

STDIO = {"command": "npx", "args": ["--package", "some-package@1.2.3", "some-bin"]}
REMOTE = {"type": "streamable-http", "url": "https://example.com/mcp", "headers": {"X-Test": "1"}}


class HomeCase(unittest.TestCase):
    """A temporary home directory with the helpers the tests share."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / ".lola").mkdir()
        self.src = self.home / ".mcp.json"
        self.dst = self.home / ".claude.json"
        self.managed = self.home / ".lola" / "init-managed-mcp.json"

    def write_json(self, path, data):
        path.write_text(json.dumps(data))

    def read_json(self, path):
        return json.loads(path.read_text())

    def set_servers(self, servers):
        self.write_json(self.src, {"mcpServers": servers})

    def run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = adapt_mcp.main(str(self.home))
        return rc, out.getvalue(), err.getvalue()

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)


class ConvertTest(unittest.TestCase):
    def test_keeps_only_known_keys(self):
        entry = {"type": "stdio", "command": "npx", "args": ["a"], "env": {"K": "v"}, "cwd": "/x", "extra": 1}
        self.assertEqual(
            adapt_mcp.convert(entry),
            {"type": "stdio", "command": "npx", "args": ["a"], "env": {"K": "v"}},
        )

    def test_command_without_type_becomes_stdio(self):
        self.assertEqual(adapt_mcp.convert({"command": "npx"}), {"command": "npx", "type": "stdio"})

    def test_explicit_type_wins_over_the_stdio_default(self):
        self.assertEqual(adapt_mcp.convert({"type": "sse", "command": "x"})["type"], "sse")

    def test_remote_entry_gets_no_type_added(self):
        entry = {"url": "https://example.com/mcp", "headers": {"A": "b"}}
        self.assertEqual(adapt_mcp.convert(entry), entry)


class SyncTest(HomeCase):
    def test_nothing_to_sync_writes_nothing(self):
        rc, out, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("no MCP servers to sync", out)
        self.assertFalse(self.dst.exists())
        self.assertFalse(self.managed.exists())

    def test_adds_servers_and_records_their_names(self):
        self.set_servers({"local": STDIO, "remote": REMOTE})
        rc, out, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("2 server(s) set, 2 added or updated, 0 removed", out)
        self.assertEqual(
            self.read_json(self.dst)["mcpServers"],
            {"local": dict(STDIO, type="stdio"), "remote": REMOTE},
        )
        self.assertEqual(self.read_json(self.managed), {"names": ["local", "remote"]})

    def test_written_files_are_private(self):
        self.set_servers({"local": STDIO})
        self.run_main()
        self.assertEqual(self.mode(self.dst), 0o600)
        self.assertEqual(self.mode(self.managed), 0o600)

    def test_other_keys_and_hand_added_servers_are_kept(self):
        mine = {"type": "stdio", "command": "mine"}
        self.write_json(self.dst, {"userKey": 1, "mcpServers": {"user-srv": mine}})
        self.set_servers({"local": STDIO})
        self.run_main()
        cfg = self.read_json(self.dst)
        self.assertEqual(cfg["userKey"], 1)
        self.assertEqual(cfg["mcpServers"]["user-srv"], mine)
        self.assertIn("local", cfg["mcpServers"])

    def test_a_removed_server_is_removed_but_a_hand_added_one_stays(self):
        mine = {"type": "stdio", "command": "mine"}
        self.write_json(self.dst, {"mcpServers": {"user-srv": mine}})
        self.set_servers({"a": STDIO, "b": REMOTE})
        self.run_main()
        self.set_servers({"a": STDIO})
        rc, out, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("1 removed", out)
        servers = self.read_json(self.dst)["mcpServers"]
        self.assertEqual(sorted(servers), ["a", "user-srv"])
        self.assertEqual(self.read_json(self.managed), {"names": ["a"]})

    def test_removing_every_server_empties_the_managed_set(self):
        self.set_servers({"a": STDIO})
        self.run_main()
        self.set_servers({})
        rc, _, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_json(self.dst)["mcpServers"], {})
        self.assertEqual(self.read_json(self.managed), {"names": []})

    def test_a_managed_name_missing_from_claude_json_counts_as_nothing_removed(self):
        self.write_json(self.managed, {"names": ["gone"]})
        self.write_json(self.dst, {"mcpServers": {}})
        self.set_servers({"a": STDIO})
        _, out, _ = self.run_main()
        self.assertIn("1 added or updated, 0 removed", out)

    def test_a_second_run_changes_nothing(self):
        self.set_servers({"local": STDIO, "remote": REMOTE})
        self.run_main()
        before = (self.dst.read_bytes(), os.stat(self.dst).st_mtime_ns,
                  self.managed.read_bytes(), os.stat(self.managed).st_mtime_ns)
        _, out, _ = self.run_main()
        after = (self.dst.read_bytes(), os.stat(self.dst).st_mtime_ns,
                 self.managed.read_bytes(), os.stat(self.managed).st_mtime_ns)
        self.assertIn("0 added or updated, 0 removed", out)
        self.assertEqual(before, after)

    def test_a_changed_entry_is_updated(self):
        self.set_servers({"srv": {"command": "one"}})
        self.run_main()
        self.set_servers({"srv": {"command": "two"}})
        _, out, _ = self.run_main()
        self.assertIn("1 added or updated", out)
        self.assertEqual(self.read_json(self.dst)["mcpServers"]["srv"]["command"], "two")

    def test_reusing_a_hand_added_name_overwrites_it(self):
        # Documented limit: a server you added by hand under a managed name is overwritten, and later removed.
        self.write_json(self.dst, {"mcpServers": {"srv": {"type": "stdio", "command": "mine"}}})
        self.set_servers({"srv": {"command": "from-lola"}})
        self.run_main()
        self.assertEqual(self.read_json(self.dst)["mcpServers"]["srv"]["command"], "from-lola")

    def test_no_temporary_files_are_left_behind(self):
        self.set_servers({"local": STDIO})
        self.run_main()
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".claude.json", ".lola", ".mcp.json"])
        self.assertEqual([p.name for p in (self.home / ".lola").iterdir()], ["init-managed-mcp.json"])


class FailureTest(HomeCase):
    """Invalid input: report, return 1, and never touch ~/.claude.json."""

    def assert_fails_and_leaves_claude_json(self, raw, expect_in_err):
        self.dst.write_text(raw)
        rc, _, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn(adapt_mcp.MARK, err)
        self.assertIn(expect_in_err, err)
        self.assertEqual(self.dst.read_text(), raw)
        self.assertFalse(self.managed.exists())

    def test_claude_json_that_is_not_json(self):
        self.set_servers({"local": STDIO})
        self.assert_fails_and_leaves_claude_json("{not json", "FAILED reading")

    def test_claude_json_that_is_not_an_object(self):
        self.set_servers({"local": STDIO})
        self.assert_fails_and_leaves_claude_json("[1]", "unexpected shape")

    def test_claude_json_with_mcp_servers_that_is_not_an_object(self):
        self.set_servers({"local": STDIO})
        self.assert_fails_and_leaves_claude_json('{"mcpServers": []}', "unexpected shape")

    def test_mcp_json_that_is_not_json(self):
        self.src.write_text("{")
        rc, _, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("FAILED reading MCP state", err)
        self.assertFalse(self.dst.exists())

    def test_mcp_json_that_is_not_an_object(self):
        self.write_json(self.src, [1])
        rc, _, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("FAILED reading MCP state", err)
        self.assertFalse(self.dst.exists())

    def test_mcp_servers_that_is_not_an_object(self):
        self.write_json(self.src, {"mcpServers": [1]})
        rc, _, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("unexpected shape", err)
        self.assertFalse(self.dst.exists())

    def test_managed_names_that_are_not_a_list(self):
        self.set_servers({"local": STDIO})
        self.write_json(self.managed, {"names": "abc"})
        rc, _, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("unexpected shape", err)
        self.assertFalse(self.dst.exists())


if __name__ == "__main__":
    unittest.main()
