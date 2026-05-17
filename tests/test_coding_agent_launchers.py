# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Coverage for the `disabled_coding_agent_launchers` traitlet + env-var
override pair, mirroring the `disabled_providers` shape.
"""

import pytest

from notebook_intelligence.util import (
    VALID_CODING_AGENT_LAUNCHERS,
    compute_effective_disabled_launchers,
    is_coding_agent_launcher_enabled_in_env,
    validate_coding_agent_launcher_ids,
)


class TestValidIds:
    def test_includes_all_five_tiles_in_kebab_case(self):
        assert set(VALID_CODING_AGENT_LAUNCHERS) == {
            "claude-code",
            "opencode",
            "pi",
            "github-copilot-cli",
            "codex",
        }

    def test_github_copilot_cli_does_not_collide_with_provider_id(self):
        # `github-copilot` is already used as a `disabled_providers` value
        # for the LLM provider; the launcher tile uses the longer
        # `github-copilot-cli` to keep the two surfaces distinct so an admin
        # can deny one without affecting the other.
        assert "github-copilot" not in VALID_CODING_AGENT_LAUNCHERS
        assert "github-copilot-cli" in VALID_CODING_AGENT_LAUNCHERS


class TestEnvVarParsing:
    def test_empty_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", raising=False)
        assert is_coding_agent_launcher_enabled_in_env("claude-code") is False

    def test_single_value_match(self, monkeypatch):
        monkeypatch.setenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", "claude-code")
        assert is_coding_agent_launcher_enabled_in_env("claude-code") is True
        assert is_coding_agent_launcher_enabled_in_env("opencode") is False

    def test_csv_match_with_whitespace(self, monkeypatch):
        monkeypatch.setenv(
            "NBI_ENABLED_CODING_AGENT_LAUNCHERS", "claude-code , opencode,pi"
        )
        assert is_coding_agent_launcher_enabled_in_env("claude-code") is True
        assert is_coding_agent_launcher_enabled_in_env("opencode") is True
        assert is_coding_agent_launcher_enabled_in_env("pi") is True
        assert is_coding_agent_launcher_enabled_in_env("codex") is False

    def test_substring_does_not_match(self, monkeypatch):
        # Pin token-equality (not substring) matching. A naive implementation
        # using `id in raw_env` would falsely report `claude` as enabled when
        # the env value is `claude-code`. The split-and-strip approach we use
        # rejects that.
        monkeypatch.setenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", "claude-code")
        assert is_coding_agent_launcher_enabled_in_env("claude") is False
        assert is_coding_agent_launcher_enabled_in_env("claude-code") is True


class TestEffectiveDisabledSet:
    """`compute_effective_disabled_launchers` resolves the traitlet plus the
    per-pod re-enable env into the wire field the frontend sees.
    """

    def test_empty_disabled_list_means_no_tiles_hidden(self):
        assert compute_effective_disabled_launchers([], allow_enabling_with_env=False) == []

    def test_traitlet_only(self):
        assert compute_effective_disabled_launchers(
            ["claude-code", "opencode"], allow_enabling_with_env=False
        ) == ["claude-code", "opencode"]

    def test_env_reenable_only_effective_when_allow_flag_set(self, monkeypatch):
        monkeypatch.setenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", "claude-code")
        assert compute_effective_disabled_launchers(
            ["claude-code"], allow_enabling_with_env=False
        ) == ["claude-code"]
        # Same env value, but the admin opted into per-pod re-enable.
        assert (
            compute_effective_disabled_launchers(
                ["claude-code"], allow_enabling_with_env=True
            )
            == []
        )

    def test_partial_env_reenable(self, monkeypatch):
        monkeypatch.setenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", "pi")
        # Disabled: opencode + pi + codex. Env re-enables only pi.
        assert compute_effective_disabled_launchers(
            ["opencode", "pi", "codex"],
            allow_enabling_with_env=True,
        ) == ["opencode", "codex"]

    def test_none_traitlet_is_safe(self, monkeypatch):
        # `disabled_coding_agent_launchers = None` is the trait's empty-state
        # sentinel; the helper's `or []` guard must keep the computation
        # from blowing up.
        monkeypatch.setenv("NBI_ENABLED_CODING_AGENT_LAUNCHERS", "any")
        assert compute_effective_disabled_launchers(None, allow_enabling_with_env=False) == []
        assert compute_effective_disabled_launchers(None, allow_enabling_with_env=True) == []


class TestStartupValidation:
    """`validate_coding_agent_launcher_ids` rejects unknown IDs at startup so
    a typo fails loudly rather than silently no-opping at request time.
    """

    def test_unknown_id_raises(self):
        with pytest.raises(ValueError, match="Unknown coding-agent launcher"):
            validate_coding_agent_launcher_ids(["claude-code", "totally-made-up"])

    def test_typo_on_github_copilot_id_raises(self):
        # The bare `github-copilot` (provider ID) is *deliberately* not a
        # valid launcher ID. An admin who copy-pastes from disabled_providers
        # should see a startup error rather than a silent no-op.
        with pytest.raises(ValueError, match="github-copilot"):
            validate_coding_agent_launcher_ids(["github-copilot"])

    def test_empty_and_none_pass(self):
        validate_coding_agent_launcher_ids([])
        validate_coding_agent_launcher_ids(None)

    def test_all_valid_ids_pass(self):
        validate_coding_agent_launcher_ids(list(VALID_CODING_AGENT_LAUNCHERS))
