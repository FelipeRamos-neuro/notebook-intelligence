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
from notebook_intelligence._claude_cli import redact_argv_for_log
from notebook_intelligence.plugin_manager import (
    PluginManager,
    is_acceptable_marketplace_source,
    is_github_marketplace_source,
)


@pytest.fixture
def fake_cli(monkeypatch):
    monkeypatch.setattr(
        "notebook_intelligence._claude_cli.resolve_claude_cli_path",
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


class TestIsGithubMarketplaceSourceEnterprise:
    """When NBI_GITHUB_ENTERPRISE_HOSTS is configured, the detector treats
    matching hostnames as GitHub for purposes of the
    ``allow_github_plugin_import`` policy gate and the GitHub-token
    injection chain.

    Token semantics (cookie-domain shape):
      - bare token (``github.acme.com``) -> exact host match only
      - leading-dot token (``.acme.com``) -> any subdomain of acme.com
    """

    @pytest.mark.parametrize(
        "source",
        [
            "https://github.acme.com/owner/repo",
            "https://github.acme.com/owner/repo.git",
            "git://github.acme.com/owner/repo",
            "ssh://git@github.acme.com/owner/repo",
            "git@github.acme.com:owner/repo",
            "git@github.acme.com:owner/repo.git",
            "HTTPS://GitHub.Acme.COM/owner/repo",  # case-insensitive URL
            "git@GitHub.Acme.COM:owner/repo",  # case-insensitive SCP
        ],
    )
    def test_recognizes_configured_ghe_host(self, source, monkeypatch):
        monkeypatch.setenv(
            "NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com"
        )
        assert is_github_marketplace_source(source) is True

    def test_exact_match_rejects_subdomain_by_default(self, monkeypatch):
        # Without a leading dot, a token is an EXACT host match. An admin
        # who lists only ``github.acme.com`` is opting OUT of subdomain
        # matching; ``ghe.acme.com`` would not pick up the GitHub-token
        # injection. This is the safer default — broad suffix matching
        # would silently inject GITHUB_TOKEN into any *.acme.com corp
        # service the user happened to point marketplace-add at.
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        assert is_github_marketplace_source("https://github.acme.com/o/r") is True
        assert is_github_marketplace_source("https://ghe.acme.com/o/r") is False
        assert is_github_marketplace_source("https://jira.acme.com/o/r") is False

    def test_leading_dot_opts_into_subdomain_match(self, monkeypatch):
        # An admin who actually wants the broad behavior writes the apex
        # with a leading dot, matching the cookie-domain convention.
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", ".acme.com")
        assert is_github_marketplace_source("https://github.acme.com/o/r") is True
        assert is_github_marketplace_source("https://ghe.acme.com/o/r") is True
        # The bare apex is NOT included; admin must list it explicitly
        # to be sure that's what they want.
        assert is_github_marketplace_source("https://acme.com/o/r") is False

    def test_lookalike_url_still_rejected(self, monkeypatch):
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        assert is_github_marketplace_source("https://example.com/o/r") is False
        # Attacker-controlled lookalike whose host is NOT the declared
        # host nor a subdomain via the leading-dot opt-in.
        assert is_github_marketplace_source("https://github.acme.com.evil.test/x") is False

    def test_lookalike_scp_still_rejected(self, monkeypatch):
        # Same lookalike-rejection property for the SCP shorthand branch,
        # which is a separate string-parsing path from the URL branch.
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        assert is_github_marketplace_source("git@github.acme.com.evil.test:o/r") is False

    def test_csv_with_multiple_hosts(self, monkeypatch):
        monkeypatch.setenv(
            "NBI_GITHUB_ENTERPRISE_HOSTS",
            "github.acme.com, ghe.example.com ,git.internal",
        )
        assert is_github_marketplace_source("https://github.acme.com/o/r") is True
        assert is_github_marketplace_source("https://ghe.example.com/o/r") is True
        assert is_github_marketplace_source("ssh://git@git.internal/o/r") is True
        # Non-listed host still rejected.
        assert is_github_marketplace_source("https://github.other.test/o/r") is False

    def test_csv_mixing_exact_and_subdomain_tokens(self, monkeypatch):
        # An admin can mix exact and subdomain tokens in one env value.
        monkeypatch.setenv(
            "NBI_GITHUB_ENTERPRISE_HOSTS",
            "github.acme.com,.example.com",
        )
        # Exact match on the first token.
        assert is_github_marketplace_source("https://github.acme.com/o/r") is True
        # Subdomain match on the second token.
        assert is_github_marketplace_source("https://ghe.example.com/o/r") is True
        # Sibling of the exact-match token is NOT matched (no leading dot).
        assert is_github_marketplace_source("https://ghe.acme.com/o/r") is False

    def test_empty_env_preserves_default_behavior(self, monkeypatch):
        # Empty env -> only public github.com is recognized. Pins
        # backward compatibility: deployments that don't set the env see
        # no change.
        monkeypatch.delenv("NBI_GITHUB_ENTERPRISE_HOSTS", raising=False)
        assert is_github_marketplace_source("https://github.acme.com/o/r") is False
        assert is_github_marketplace_source("https://github.com/o/r") is True

    def test_trailing_dot_fqdn_still_matches(self, monkeypatch):
        # urllib.parse.urlparse preserves the trailing dot on
        # `github.com.`, which would otherwise bypass exact-match
        # detection. The host-normalize helper strips the trailing dot.
        monkeypatch.delenv("NBI_GITHUB_ENTERPRISE_HOSTS", raising=False)
        assert is_github_marketplace_source("https://github.com./o/r") is True
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        assert is_github_marketplace_source("https://github.acme.com./o/r") is True
        assert is_github_marketplace_source("git@github.acme.com.:o/r") is True

    def test_url_with_embedded_userinfo_is_rejected(self, monkeypatch):
        # `https://user@github.com/o/r` resolves to host=github.com via
        # urlparse, but a non-`git` userinfo is the lookalike-attack
        # shape. The detector refuses to authorize token injection on
        # such URLs even though the host extracted is github.com.
        monkeypatch.delenv("NBI_GITHUB_ENTERPRISE_HOSTS", raising=False)
        assert (
            is_github_marketplace_source(
                "https://attacker@github.com/owner/repo"
            )
            is False
        )
        assert (
            is_github_marketplace_source(
                "https://user:pw@github.com/owner/repo"
            )
            is False
        )
        # `git` is the conventional SSH user for git, so ssh://git@...
        # remains accepted (regression guard for the legitimate path).
        assert (
            is_github_marketplace_source("ssh://git@github.com/o/r") is True
        )


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

    def test_list_marketplace_plugins_reads_cached_manifest(
        self, tmp_path, monkeypatch
    ):
        plugins_root = tmp_path / "plugins"
        manifest = (
            plugins_root
            / "marketplaces"
            / "acme"
            / ".claude-plugin"
            / "marketplace.json"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "plugins": [
                        {
                            "name": "alpha",
                            "description": "Alpha plugin",
                            "source": "./plugins/alpha",
                        },
                        {"name": "beta", "source": "./plugins/beta"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(plugins_root))

        manager = PluginManager()

        assert asyncio.run(manager.list_marketplace_plugins("acme")) == [
            {
                "name": "alpha",
                "description": "Alpha plugin",
                "source": "./plugins/alpha",
            },
            {"name": "beta", "source": "./plugins/beta"},
        ]

    def test_list_marketplace_plugins_rejects_path_traversal(self):
        manager = PluginManager()

        with pytest.raises(ValueError, match="Invalid marketplace name"):
            asyncio.run(manager.list_marketplace_plugins("..\\evil"))

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
        ],
    )
    def test_list_plugins_handles_wrapper_shapes(
        self, fake_cli, monkeypatch, wrapper
    ):
        _stub_subprocess(monkeypatch, captured={}, stdout=wrapper)
        manager = PluginManager()
        assert asyncio.run(manager.list_plugins()) == [{"name": "a"}]

    def test_list_plugins_rejects_generic_data_wrapper(
        self, fake_cli, monkeypatch
    ):
        # `data` is generic enough to mean a metadata envelope rather than
        # a plugin list, so the parser does not walk into it.
        _stub_subprocess(
            monkeypatch, captured={}, stdout=b'{"data":[{"name":"a"}]}'
        )
        manager = PluginManager()
        with pytest.raises(ValueError, match="Unrecognized JSON shape"):
            asyncio.run(manager.list_plugins())

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

    def test_add_marketplace_injects_github_token_for_github_source(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        # Force the token resolver to return a known value (skip env / gh).
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: "ghp_testtoken",
        )
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(source="acme/repo", scope="user")
        )
        # Token flows through env, not argv — kept out of DEBUG logs.
        env = captured["kwargs"]["env"]
        assert env["GITHUB_TOKEN"] == "ghp_testtoken"
        assert env["GH_TOKEN"] == "ghp_testtoken"
        assert "ghp_testtoken" not in " ".join(captured["argv"])

    def test_add_marketplace_blocks_ghe_url_when_disabled(
        self, fake_cli, monkeypatch
    ):
        # End-to-end pin: the policy gate at add_marketplace -> detector
        # closes for GHE URLs when allow_github_plugin_import is False.
        # Without the GHE-aware detector this would silently fall through
        # to "not GitHub" and reach the CLI shell-out.
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        _stub_subprocess(monkeypatch, captured={})
        manager = PluginManager()
        with pytest.raises(PermissionError, match="GitHub"):
            asyncio.run(
                manager.add_marketplace(
                    source="https://github.acme.com/owner/repo",
                    scope="user",
                    allow_github=False,
                )
            )

    def test_add_marketplace_injects_token_for_ghe_source(
        self, fake_cli, monkeypatch
    ):
        # End-to-end pin: GHE URLs go through the token-injection chain
        # when the host is declared. Without the detector extension,
        # the env block would be empty and the git auth would fall
        # through to anonymous (which fails for private GHE).
        monkeypatch.setenv("NBI_GITHUB_ENTERPRISE_HOSTS", "github.acme.com")
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: "ghp_ghetoken",
        )
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(
                source="https://github.acme.com/owner/repo", scope="user"
            )
        )
        env = captured["kwargs"]["env"]
        assert env["GITHUB_TOKEN"] == "ghp_ghetoken"
        assert env["GH_TOKEN"] == "ghp_ghetoken"
        assert "ghp_ghetoken" not in " ".join(captured["argv"])

    def test_add_marketplace_skips_token_injection_for_local_source(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: "ghp_testtoken",
        )
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(
                source="/abs/path/marketplace", scope="user"
            )
        )
        # Local path → no env-overrides → subprocess inherits parent env
        # unchanged (None signals "don't override"). The token shouldn't
        # leak into requests for non-GitHub sources.
        assert captured["kwargs"]["env"] is None

    def test_add_marketplace_skips_token_injection_when_unavailable(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: None,
        )
        # Belt-and-suspenders: scrub real GitHub envs so a stale or
        # accidentally-broken patch can't leak the developer's token into
        # the test process.
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(source="acme/repo", scope="user")
        )
        assert captured["kwargs"]["env"] is None

    def test_add_marketplace_skips_token_for_non_github_https(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: "ghp_should_not_be_used",
        )
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(
                source="https://example.com/manifest", scope="user"
            )
        )
        # Non-GitHub HTTPS source must not receive a github.com PAT.
        assert captured["kwargs"]["env"] is None

    def test_add_marketplace_env_overrides_win_over_inherited(
        self, fake_cli, monkeypatch
    ):
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        monkeypatch.setenv("GITHUB_TOKEN", "stale_from_parent_env")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: "fresh_resolved",
        )
        manager = PluginManager()
        asyncio.run(
            manager.add_marketplace(source="acme/repo", scope="user")
        )
        env = captured["kwargs"]["env"]
        # Override wins on collision.
        assert env["GITHUB_TOKEN"] == "fresh_resolved"
        # Inherited keys survive the merge.
        assert env["PATH"] == "/usr/bin:/bin"

    def test_add_marketplace_remediation_hint_on_auth_failure(
        self, fake_cli, monkeypatch
    ):
        # CLI fails with a typical git-auth-missing message; we should
        # wrap the error with an actionable hint when the source is
        # GitHub and we had no token to inject.
        async def fake_subprocess(*argv, **kwargs):
            proc = MagicMock()

            async def communicate():
                return (b"", b"fatal: could not read Username for 'https://github.com'")

            proc.communicate = communicate
            proc.returncode = 1
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        monkeypatch.setattr(
            "notebook_intelligence.plugin_manager.resolve_github_token",
            lambda: None,
        )
        manager = PluginManager()
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            asyncio.run(
                manager.add_marketplace(source="acme/repo", scope="user")
            )

    def test_github_gate_independent_of_policy_gate(
        self, fake_cli, monkeypatch
    ):
        """The two gates compose: ``allow_github=False`` rejects a GitHub
        source with ``PermissionError`` regardless of policy state, and a
        non-GitHub source with ``allow_github=False`` still proceeds. The
        policy gate (force-off → 403 in the handler's ``prepare()``)
        sits *above* the manager and is independent — exercised by
        ``test_policy_gate.py``. This test pins the manager-layer
        contract: ``allow_github`` is the only thing that affects the
        manager's permission decision."""
        captured: dict = {}
        _stub_subprocess(monkeypatch, captured=captured)
        manager = PluginManager()

        # GitHub source + allow_github=False → PermissionError, no CLI call.
        captured.clear()
        with pytest.raises(PermissionError):
            asyncio.run(
                manager.add_marketplace(
                    source="acme/repo", scope="user", allow_github=False
                )
            )
        assert captured.get("argv") is None

        # Non-GitHub source + allow_github=False → CLI call proceeds.
        asyncio.run(
            manager.add_marketplace(
                source="https://example.com/manifest",
                scope="user",
                allow_github=False,
            )
        )
        assert captured["argv"][1:5] == [
            "plugin",
            "marketplace",
            "add",
            "--scope",
        ]

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
        async def fake_subprocess(*argv, **kwargs):
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

    def test_cli_failure_scrubs_injected_token_from_stderr(
        self, fake_cli, monkeypatch
    ):
        # If a downstream tool (git, curl in verbose mode, a misconfigured
        # credential helper) echoes the injected token into stderr, the
        # ValueError surfaced to the browser via `_error()` would otherwise
        # leak the secret. `_claude_cli` must scrub `env_overrides` values
        # before raising.
        secret = "ghp_supersecrettoken1234567890"

        async def fake_subprocess(*argv, **kwargs):
            proc = MagicMock()

            async def communicate():
                return (
                    b"",
                    f"auth failed: tried {secret} but expired".encode(),
                )

            proc.communicate = communicate
            proc.returncode = 1
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        monkeypatch.setenv("GITHUB_TOKEN", secret)
        manager = PluginManager()
        try:
            asyncio.run(
                manager.add_marketplace(
                    source="https://github.com/owner/repo",
                    scope="user",
                    allow_github=True,
                )
            )
        except ValueError as exc:
            assert secret not in str(exc), "Token leaked in error message"
            assert "<redacted>" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_passes_cwd_to_cli(self, fake_cli, monkeypatch):
        # Project-scope writes must resolve against the user's working
        # directory, not the Jupyter server's cwd. A missing cwd means
        # `--scope project` lands the manifest in the wrong tree.
        captured: dict = {}

        async def fake_subprocess(*argv, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            proc = MagicMock()

            async def communicate():
                return (b"[]", b"")

            proc.communicate = communicate
            proc.returncode = 0
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        manager = PluginManager(working_dir="/home/user/work")
        asyncio.run(manager.list_plugins())
        assert captured["cwd"] == "/home/user/work"

    def test_missing_cli_raises_filenotfound(self, monkeypatch):
        monkeypatch.setattr(
            "notebook_intelligence._claude_cli.resolve_claude_cli_path",
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
        assert redact_argv_for_log(argv) == [
            "claude",
            "plugin",
            "install",
            "-e",
            "<redacted>",
            "-H",
            "<redacted>",
        ]

    @pytest.mark.parametrize(
        "flag",
        [
            "--client-secret",
            "--client-id",
            "--token",
            "--password",
            "--auth",
            "--bearer",
        ],
    )
    def test_redacts_forward_compat_secret_flags(self, flag):
        argv = ["claude", "plugin", "install", flag, "supersecret", "name"]
        assert redact_argv_for_log(argv) == [
            "claude",
            "plugin",
            "install",
            flag,
            "<redacted>",
            "name",
        ]

    def test_passes_through_non_secret_flags(self):
        argv = ["claude", "plugin", "install", "--scope", "user", "name"]
        assert redact_argv_for_log(argv) == argv


# Per-family policy-gate coverage lives in `tests/test_policy_gate.py`,
# parametrized across SkillsBaseHandler / ClaudeMCPBaseHandler /
# PluginsBaseHandler.
