# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the ClaudeMCPManager and the management policy gate."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import notebook_intelligence.extension as ext_module
from notebook_intelligence.claude_mcp_manager import (
    ClaudeMCPManager,
    ClaudeMCPServer,
)
from notebook_intelligence.extension import (
    ClaudeMCPBaseHandler,
    ClaudeMCPListHandler,
)


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """Stand up a fake $HOME with a populated `~/.claude.json`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def working_dir(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    return cwd


def _write_claude_json(home: Path, doc: dict) -> None:
    (home / ".claude.json").write_text(json.dumps(doc), encoding="utf-8")


class TestClaudeMCPManagerListServers:
    def test_empty_when_no_config(self, claude_home, working_dir):
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        assert manager.list_servers() == []

    def test_user_scope_servers_round_trip(self, claude_home, working_dir):
        _write_claude_json(
            claude_home,
            {
                "mcpServers": {
                    "voicemode": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--refresh", "voice-mode"],
                        "env": {},
                    },
                    "sentry": {
                        "type": "http",
                        "url": "https://mcp.sentry.dev/mcp",
                        "headers": {"X-Tenant": "acme"},
                    },
                }
            },
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        servers = manager.list_servers()
        names = [(s.name, s.scope, s.transport) for s in servers]
        assert names == [
            ("sentry", "user", "http"),
            ("voicemode", "user", "stdio"),
        ]
        sentry = next(s for s in servers if s.name == "sentry")
        assert sentry.url == "https://mcp.sentry.dev/mcp"
        assert sentry.headers == {"X-Tenant": "acme"}
        voicemode = next(s for s in servers if s.name == "voicemode")
        assert voicemode.command == "uvx"
        assert voicemode.args == ["--refresh", "voice-mode"]

    def test_local_scope_keyed_by_working_dir(self, claude_home, working_dir):
        _write_claude_json(
            claude_home,
            {
                "projects": {
                    str(working_dir): {
                        "mcpServers": {
                            "playwright": {
                                "type": "stdio",
                                "command": "npx",
                                "args": ["-y", "@playwright/mcp@latest"],
                            }
                        }
                    },
                    "/some/other/project": {
                        "mcpServers": {
                            "should-not-appear": {
                                "type": "stdio",
                                "command": "x",
                            }
                        }
                    },
                }
            },
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        servers = manager.list_servers()
        assert [(s.name, s.scope) for s in servers] == [("playwright", "local")]

    def test_project_scope_from_dot_mcp_json(self, claude_home, working_dir):
        (working_dir / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "type": "stdio",
                            "command": "shared-cmd",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        servers = manager.list_servers()
        assert [(s.name, s.scope) for s in servers] == [("shared", "project")]

    def test_all_three_scopes_merged(self, claude_home, working_dir):
        _write_claude_json(
            claude_home,
            {
                "mcpServers": {
                    "u-srv": {"type": "stdio", "command": "u"},
                },
                "projects": {
                    str(working_dir): {
                        "mcpServers": {
                            "l-srv": {"type": "stdio", "command": "l"},
                        }
                    }
                },
            },
        )
        (working_dir / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"p-srv": {"type": "stdio", "command": "p"}}}),
            encoding="utf-8",
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        scopes = {s.name: s.scope for s in manager.list_servers()}
        assert scopes == {"u-srv": "user", "l-srv": "local", "p-srv": "project"}

    def test_unknown_transport_passes_through(self, claude_home, working_dir):
        _write_claude_json(
            claude_home,
            {"mcpServers": {"weird": {"type": "websocket", "url": "wss://x"}}},
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        srv = manager.list_servers()[0]
        assert srv.transport == "websocket"


class TestClaudeMCPManagerWrites:
    @pytest.mark.parametrize("scope", ["user", "project", "local"])
    def test_add_invokes_cli_with_correct_args(
        self, claude_home, working_dir, scope, monkeypatch
    ):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: "/usr/local/bin/claude",
        )
        captured: dict = {}

        async def fake_subprocess(*argv, cwd, stdin, stdout, stderr):
            captured["argv"] = list(argv)
            captured["cwd"] = cwd
            proc = MagicMock()

            async def communicate():
                return (b"ok", b"")

            proc.communicate = communicate
            proc.returncode = 0
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        # The post-add re-read should find the server too.
        _write_claude_json(claude_home, {"mcpServers": {}})
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        result = asyncio.run(
            manager.add_server(
                name="my-srv",
                scope=scope,
                transport="stdio",
                command_or_url="npx",
                args=["-y", "pkg"],
                env={"K": "v"},
            )
        )
        assert captured["argv"][0] == "/usr/local/bin/claude"
        assert "mcp" in captured["argv"]
        assert "add" in captured["argv"]
        assert "--scope" in captured["argv"]
        scope_idx = captured["argv"].index("--scope")
        assert captured["argv"][scope_idx + 1] == scope
        assert "-e" in captured["argv"]
        assert "K=v" in captured["argv"]
        assert captured["argv"][-3:] == ["npx", "--", "-y"] or captured["argv"][
            -4:
        ] == ["npx", "--", "-y", "pkg"]
        # When the CLI succeeds but our re-read finds nothing, we synthesize
        # from the inputs rather than 500.
        assert result.name == "my-srv"
        assert result.scope == scope

    def test_remove_invokes_cli(self, claude_home, working_dir, monkeypatch):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: "/usr/local/bin/claude",
        )
        captured: dict = {}

        async def fake_subprocess(*argv, cwd, stdin, stdout, stderr):
            captured["argv"] = list(argv)
            proc = MagicMock()

            async def communicate():
                return (b"", b"")

            proc.communicate = communicate
            proc.returncode = 0
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        manager = ClaudeMCPManager(working_dir=str(working_dir))
        asyncio.run(manager.remove_server("my-srv", "user"))
        assert captured["argv"][1:] == [
            "mcp",
            "remove",
            "--scope",
            "user",
            "my-srv",
        ]

    def test_cli_failure_raises_with_stderr(
        self, claude_home, working_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: "/usr/local/bin/claude",
        )

        async def fake_subprocess(*argv, cwd, stdin, stdout, stderr):
            proc = MagicMock()

            async def communicate():
                return (b"", b"already exists")

            proc.communicate = communicate
            proc.returncode = 1
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        manager = ClaudeMCPManager(working_dir=str(working_dir))
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(manager.remove_server("name", "user"))

    def test_missing_cli_raises_filenotfound(
        self, claude_home, working_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: None,
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        with pytest.raises(FileNotFoundError):
            asyncio.run(manager.remove_server("name", "user"))

    def test_leading_dash_name_rejected(self, claude_home, working_dir, monkeypatch):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: "/usr/local/bin/claude",
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        with pytest.raises(ValueError, match="leading '-'"):
            asyncio.run(
                manager.add_server(
                    name="--scope=user",
                    scope="user",
                    transport="stdio",
                    command_or_url="x",
                )
            )

    def test_leading_dash_command_rejected(
        self, claude_home, working_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "notebook_intelligence.claude_mcp_manager.resolve_claude_cli_path",
            lambda: "/usr/local/bin/claude",
        )
        manager = ClaudeMCPManager(working_dir=str(working_dir))
        with pytest.raises(ValueError, match="leading '-'"):
            asyncio.run(
                manager.add_server(
                    name="srv",
                    scope="user",
                    transport="stdio",
                    command_or_url="--whatever",
                )
            )


class TestRedactArgvForLog:
    def test_redacts_env_values(self):
        from notebook_intelligence.claude_mcp_manager import _redact_argv_for_log

        argv = ["claude", "mcp", "add", "-e", "API_KEY=sk-secret", "name", "cmd"]
        assert _redact_argv_for_log(argv) == [
            "claude",
            "mcp",
            "add",
            "-e",
            "<redacted>",
            "name",
            "cmd",
        ]

    def test_redacts_header_values(self):
        from notebook_intelligence.claude_mcp_manager import _redact_argv_for_log

        argv = ["claude", "mcp", "add", "-H", "Authorization: Bearer abc"]
        assert _redact_argv_for_log(argv)[-2:] == ["-H", "<redacted>"]

    def test_passes_through_non_secrets(self):
        from notebook_intelligence.claude_mcp_manager import _redact_argv_for_log

        argv = ["claude", "mcp", "add", "--scope", "user", "name", "npx"]
        assert _redact_argv_for_log(argv) == argv


class TestClaudeMCPManagementPolicyGate:
    """Mirror of TestSkillsManagementPolicyGate — ensures the prepare()
    chokepoint short-circuits with 403 when force-off."""

    @staticmethod
    def _run_prepare(handler):
        from jupyter_server.base.handlers import APIHandler

        async def _noop(_self):
            return None

        with patch.object(APIHandler, "prepare", _noop):
            asyncio.run(ClaudeMCPBaseHandler.prepare(handler))

    def test_default_attribute_allows(self):
        assert ClaudeMCPBaseHandler.claude_mcp_management_enabled is True

    def test_prepare_rejects_when_disabled(self):
        handler = MagicMock(spec=ClaudeMCPListHandler)
        handler._finished = False
        handler.claude_mcp_management_enabled = False

        def _finish(payload):
            handler._finished = True
            handler._finish_payload = payload

        handler.finish.side_effect = _finish
        self._run_prepare(handler)
        handler.set_status.assert_called_with(403)
        body = json.loads(handler._finish_payload)
        assert "administrator" in body["error"].lower()

    def test_prepare_passes_when_enabled(self):
        handler = MagicMock(spec=ClaudeMCPListHandler)
        handler._finished = False
        handler.claude_mcp_management_enabled = True
        self._run_prepare(handler)
        handler.set_status.assert_not_called()
        handler.finish.assert_not_called()
