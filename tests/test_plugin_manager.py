# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the PluginManager and the plugins management policy gate."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from notebook_intelligence.extension import (
    PluginsBaseHandler,
    PluginsListHandler,
)
from notebook_intelligence.plugin_manager import (
    PluginManager,
    is_github_marketplace_source,
    is_acceptable_marketplace_source,
    _redact_argv_for_log,
)


@pytest.fixture
def fake_cli(monkeypatch):
    monkeypatch.setattr(
        "notebook_intelligence.plugin_manager.resolve_claude_cli_path",
        lambda: "/usr/local/bin/claude",
    )


from tests.conftest import stub_claude_subprocess as _stub_subprocess  # noqa: E402


class TestIsGithubMarketplaceSource:
    @pytest.mark.parametrize(
        "source",
        [
            "github:owner/repo",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "http://github.com/owner/repo",
            "git@github.com:owner/repo.git",
            "owner/repo",
            "owner/repo/",  # trailing slash on shorthand
            "owner/repo.git",  # .git suffix on shorthand
            "https://github.com:443/owner/repo",  # explicit port
            "HTTPS://GitHub.com/owner/repo",  # case variation
            "https://www.github.com/owner/repo",  # www subdomain
            "git://github.com/owner/repo",  # deprecated git protocol
            "ssh://git@github.com/owner/repo",  # ssh protocol
        ],
    )
    def test_recognizes_github(self, source):
        assert is_github_marketplace_source(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "/abs/path/to/marketplace",
            "./relative/path",
            "~/home/path",
            "https://example.com/marketplace",
            "owner/repo/extra",  # too many slashes — not bare shorthand
            "https://github.org/owner/repo",  # similar but not GitHub
            "https://gitfake.com/owner/repo",
            "",
        ],
    )
    def test_rejects_non_github(self, source):
        assert is_github_marketplace_source(source) is False


class TestIsAcceptableMarketplaceSource:
    @pytest.mark.parametrize(
        "source",
        [
            "https://example.com/manifest",
            "https://github.com/owner/repo",
            "github:owner/repo",
            "ssh://git@github.com/owner/repo",
            "git://github.com/owner/repo",
            "git@github.com:owner/repo",
            "owner/repo",
            "/abs/path/to/marketplace",
            "./relative/path",
            "~/home/path",
        ],
    )
    def test_accepts(self, source):
        assert is_acceptable_marketplace_source(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "http://internal.attacker/manifest",  # plain http blocked (SSRF)
            "http://169.254.169.254/latest/",  # IMDS-style probe
            "file:///etc/passwd",
            "ftp://example.com/manifest",
            "gopher://example.com",
            "git://example.com/repo",  # non-github git://
            "ssh://git@gitlab.com/owner/repo",  # non-github ssh://
            "",
        ],
    )
    def test_rejects(self, source):
        assert is_acceptable_marketplace_source(source) is False


class TestPluginManagerReads:
    def test_list_plugins_parses_json_array(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(
            monkeypatch,
            captured=captured,
            stdout=b'[{"name":"a","scope":"user","enabled":true}]',
        )
        manager = PluginManager()
        result = asyncio.run(manager.list_plugins())
        assert result == [{"name": "a", "scope": "user", "enabled": True}]
        assert captured["argv"][1:] == ["plugin", "list", "--json"]

    def test_list_marketplaces_parses_json_array(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(
            monkeypatch,
            captured=captured,
            stdout=b'[{"name":"acme","source":"github:acme/marketplace"}]',
        )
        manager = PluginManager()
        result = asyncio.run(manager.list_marketplaces())
        assert result == [{"name": "acme", "source": "github:acme/marketplace"}]

    def test_list_plugins_handles_installed_object_shape(self, fake_cli, monkeypatch):
        # `claude plugin list --available --json` returns an object — the
        # bare `--json` form returns an array, but tolerate both.
        _stub_subprocess(
            monkeypatch,
            captured={},
            stdout=json.dumps(
                {"installed": [{"name": "a"}], "available": [{"name": "b"}]}
            ).encode(),
        )
        manager = PluginManager()
        result = asyncio.run(manager.list_plugins())
        assert result == [{"name": "a"}]

    def test_list_plugins_empty_output(self, fake_cli, monkeypatch):
        _stub_subprocess(monkeypatch, captured={}, stdout=b"")
        manager = PluginManager()
        assert asyncio.run(manager.list_plugins()) == []

    def test_list_plugins_invalid_json_raises(self, fake_cli, monkeypatch):
        _stub_subprocess(monkeypatch, captured={}, stdout=b"not json")
        manager = PluginManager()
        with pytest.raises(ValueError, match="Could not parse"):
            asyncio.run(manager.list_plugins())

    @pytest.mark.parametrize(
        "wrapper",
        [
            b'{"installed":[{"name":"a"}]}',
            b'{"plugins":[{"name":"a"}]}',
            b'{"items":[{"name":"a"}]}',
            b'{"data":[{"name":"a"}]}',
        ],
    )
    def test_list_plugins_handles_wrapper_shapes(
        self, fake_cli, monkeypatch, wrapper
    ):
        _stub_subprocess(monkeypatch, captured={}, stdout=wrapper)
        manager = PluginManager()
        assert asyncio.run(manager.list_plugins()) == [{"name": "a"}]

    def test_list_plugins_unknown_shape_raises(self, fake_cli, monkeypatch):
        # Surface CLI version skew explicitly rather than masquerading as
        # "no plugins installed".
        _stub_subprocess(
            monkeypatch, captured={}, stdout=b'{"unknownKey":[{"name":"a"}]}'
        )
        manager = PluginManager()
        with pytest.raises(ValueError, match="Unrecognized JSON shape"):
            asyncio.run(manager.list_plugins())


class TestPluginManagerWrites:
    def test_install_invokes_cli_with_scope(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(manager.install_plugin(plugin="my-plugin", scope="project"))
        assert captured["argv"][1:] == [
            "plugin",
            "install",
            "--scope",
            "project",
            "my-plugin",
        ]

    def test_uninstall_passes_yes_flag_for_non_tty(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(manager.uninstall_plugin(plugin="my-plugin", scope="user"))
        assert captured["argv"][1:] == [
            "plugin",
            "uninstall",
            "--scope",
            "user",
            "-y",
            "my-plugin",
        ]

    @pytest.mark.parametrize("enabled,verb", [(True, "enable"), (False, "disable")])
    def test_set_plugin_enabled_invokes_correct_verb(
        self, fake_cli, monkeypatch, enabled, verb
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(
            manager.set_plugin_enabled(plugin="p", enabled=enabled, scope="user")
        )
        assert captured["argv"][1:] == ["plugin", verb, "--scope", "user", "p"]

    def test_set_plugin_enabled_omits_scope_when_none(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(manager.set_plugin_enabled(plugin="p", enabled=True))
        assert captured["argv"][1:] == ["plugin", "enable", "p"]

    def test_add_marketplace_passes_source(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(manager.add_marketplace(source="acme/repo", scope="user"))
        assert captured["argv"][1:] == [
            "plugin",
            "marketplace",
            "add",
            "--scope",
            "user",
            "acme/repo",
        ]

    def test_add_marketplace_blocks_github_when_disabled(
        self, fake_cli, monkeypatch
    ):
        _stub_subprocess(monkeypatch, captured={})
        manager = PluginManager()
        with pytest.raises(PermissionError, match="GitHub"):
            asyncio.run(
                manager.add_marketplace(
                    source="acme/repo", scope="user", allow_github=False
                )
            )

    def test_add_marketplace_allows_non_github_when_disabled(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(
                source="https://example.com/marketplace",
                scope="user",
                allow_github=False,
            )
        )
        assert "marketplace" in captured["argv"]

    def test_remove_marketplace_invokes_cli(self, fake_cli, monkeypatch):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()
        asyncio.run(manager.remove_marketplace(name="acme"))
        assert captured["argv"][1:] == [
            "plugin",
            "marketplace",
            "remove",
            "acme",
        ]

    def test_cli_failure_raises_with_stderr(self, fake_cli, monkeypatch):
        async def fake_subprocess(*argv, stdin, stdout, stderr):
            proc = MagicMock()

            async def communicate():
                return (b"", b"plugin not found")

            proc.communicate = communicate
            proc.returncode = 1
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        manager = PluginManager()
        with pytest.raises(ValueError, match="plugin not found"):
            asyncio.run(manager.uninstall_plugin(plugin="x", scope="user"))

    def test_missing_cli_raises_filenotfound(self, monkeypatch):
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_claude_cli_path",
            lambda: None,
        )
        manager = PluginManager()
        with pytest.raises(FileNotFoundError):
            asyncio.run(manager.list_plugins())

    @pytest.mark.parametrize(
        "field,value",
        [
            ("plugin", "--evil"),
            ("source", "-x"),
            ("name", "--remove-everything"),
        ],
    )
    def test_leading_dash_rejected(self, fake_cli, monkeypatch, field, value):
        _stub_subprocess(monkeypatch, captured={})
        manager = PluginManager()
        with pytest.raises(ValueError, match="leading '-'"):
            if field == "plugin":
                asyncio.run(manager.install_plugin(plugin=value, scope="user"))
            elif field == "source":
                asyncio.run(manager.add_marketplace(source=value, scope="user"))
            else:
                asyncio.run(manager.remove_marketplace(name=value))


class TestLockSerialization:
    """Two concurrent writes against the (process-shared) lock must not
    interleave at the subprocess layer. Catches a regression where the
    asyncio.Lock gets demoted back to instance-level."""

    def test_concurrent_install_calls_are_serialized(self, fake_cli, monkeypatch):
        events: list[str] = []

        async def fake_subprocess(*argv, **kwargs):
            proc = MagicMock()
            tag = argv[-1]

            async def communicate():
                events.append(f"start:{tag}")
                await asyncio.sleep(0.01)
                events.append(f"end:{tag}")
                return (b"", b"")

            proc.communicate = communicate
            proc.returncode = 0
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        async def driver():
            m1 = PluginManager()
            m2 = PluginManager()
            await asyncio.gather(
                m1.install_plugin(plugin="A", scope="user"),
                m2.install_plugin(plugin="B", scope="user"),
            )

        asyncio.run(driver())
        # No interleaving: each start is followed by its own end before the
        # next start. We don't assert which won the race, just that they
        # didn't overlap.
        assert len(events) == 4
        assert events[0].startswith("start:")
        assert events[1] == events[0].replace("start:", "end:")
        assert events[2].startswith("start:")
        assert events[3] == events[2].replace("start:", "end:")


class TestRedactArgvForLog:
    def test_redacts_env_and_header_values(self):
        argv = ["claude", "plugin", "install", "-e", "K=v", "-H", "Auth: token"]
        assert _redact_argv_for_log(argv) == [
            "claude",
            "plugin",
            "install",
            "-e",
            "<redacted>",
            "-H",
            "<redacted>",
        ]


class TestPluginsManagementPolicyGate:
    @staticmethod
    def _run_prepare(handler):
        from jupyter_server.base.handlers import APIHandler

        async def _noop(_self):
            return None

        with patch.object(APIHandler, "prepare", _noop):
            asyncio.run(PluginsBaseHandler.prepare(handler))

    def test_default_attribute_allows(self):
        assert PluginsBaseHandler.plugins_management_enabled is True

    def test_prepare_rejects_when_disabled(self):
        handler = MagicMock(spec=PluginsListHandler)
        handler._finished = False
        handler.policy_enabled_attr = "plugins_management_enabled"
        handler.policy_disabled_message = (
            "Plugins management is disabled by your administrator"
        )
        handler.plugins_management_enabled = False

        def _finish(payload):
            handler._finished = True
            handler._finish_payload = payload

        handler.finish.side_effect = _finish
        self._run_prepare(handler)
        handler.set_status.assert_called_with(403)
        body = json.loads(handler._finish_payload)
        assert "administrator" in body["error"].lower()

    def test_prepare_passes_when_enabled(self):
        handler = MagicMock(spec=PluginsListHandler)
        handler._finished = False
        handler.policy_enabled_attr = "plugins_management_enabled"
        handler.plugins_management_enabled = True
        self._run_prepare(handler)
        handler.set_status.assert_not_called()
        handler.finish.assert_not_called()
