# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the Claude permission-mode selector backend (issue #359)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import notebook_intelligence.claude as claude_module
from notebook_intelligence.claude import (
    CLAUDE_BYPASS_PERMISSION_MODE,
    CLAUDE_PERMISSION_MODES,
    DEFAULT_PERMISSION_MODE,
    apply_permission_mode,
    claude_bypass_disabled_by_managed_settings,
    claude_managed_default_permission_mode,
    custom_permission_handler,
    reset_permission_mode_tracking,
    resolve_permission_mode,
    set_permission_mode_notifier,
)
from claude_agent_sdk import PermissionResultAllow
from notebook_intelligence.extension import (
    FEATURE_POLICY_DEFAULTS,
    _build_feature_policies_response,
    _resolve_default_permission_mode,
)
from notebook_intelligence.feature_flags import POLICY_FORCE_OFF, POLICY_USER_CHOICE


@pytest.fixture(autouse=True)
def _no_managed_settings(monkeypatch, tmp_path):
    """Point the managed-settings probe at a tmp location by default.

    Without this, a developer machine that actually has enterprise managed
    settings installed would bleed into every assertion below. Tests that
    need a managed file write to ``managed_path`` and re-point the tuple.
    """
    managed = tmp_path / "managed-settings.json"
    monkeypatch.setattr(claude_module, "_MANAGED_SETTINGS_PATHS", (str(managed),))
    yield managed


@pytest.fixture(autouse=True)
def _reset_tracking():
    set_permission_mode_notifier(None)
    claude_module._current_permission_mode = DEFAULT_PERMISSION_MODE
    yield
    set_permission_mode_notifier(None)
    claude_module._current_permission_mode = DEFAULT_PERMISSION_MODE


class TestResolvePermissionMode:
    @pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
    def test_normal_modes_pass_regardless_of_policy(self, mode):
        assert resolve_permission_mode(mode, False) == mode
        assert resolve_permission_mode(mode, True) == mode

    @pytest.mark.parametrize(
        "requested", ["dontAsk", "auto", "", "DEFAULT", "Plan", None, 42]
    )
    def test_unknown_modes_collapse_to_default(self, requested):
        assert resolve_permission_mode(requested, True) == DEFAULT_PERMISSION_MODE

    def test_bypass_rejected_when_policy_disallows(self):
        assert (
            resolve_permission_mode(CLAUDE_BYPASS_PERMISSION_MODE, False)
            == DEFAULT_PERMISSION_MODE
        )

    def test_bypass_allowed_when_policy_allows(self):
        assert (
            resolve_permission_mode(CLAUDE_BYPASS_PERMISSION_MODE, True)
            == CLAUDE_BYPASS_PERMISSION_MODE
        )

    def test_bypass_rejected_when_managed_settings_disable_it(
        self, _no_managed_settings
    ):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"disableBypassPermissionsMode": True}})
        )
        assert (
            resolve_permission_mode(CLAUDE_BYPASS_PERMISSION_MODE, True)
            == DEFAULT_PERMISSION_MODE
        )


class TestManagedSettings:
    def test_absent_file_means_unmanaged(self):
        assert claude_bypass_disabled_by_managed_settings() is False
        assert claude_managed_default_permission_mode() is None

    def test_corrupt_file_fails_closed(self, _no_managed_settings):
        _no_managed_settings.write_text("{not json")
        assert claude_bypass_disabled_by_managed_settings() is True

    def test_managed_file_without_permissions_block(self, _no_managed_settings):
        _no_managed_settings.write_text(json.dumps({"env": {}}))
        assert claude_bypass_disabled_by_managed_settings() is False
        assert claude_managed_default_permission_mode() is None

    def test_default_mode_read_when_valid(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"defaultMode": "acceptEdits"}})
        )
        assert claude_managed_default_permission_mode() == "acceptEdits"

    def test_invalid_default_mode_ignored(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"defaultMode": "yolo"}})
        )
        assert claude_managed_default_permission_mode() is None


class TestApplyPermissionMode:
    def _sdk_client(self):
        client = MagicMock()
        client.set_permission_mode = AsyncMock()
        return client

    def test_applies_and_tracks_new_mode(self):
        client = self._sdk_client()
        changed = asyncio.run(apply_permission_mode(client, "plan"))
        assert changed is True
        client.set_permission_mode.assert_awaited_once_with("plan")
        assert claude_module.get_current_permission_mode() == "plan"

    def test_noop_when_mode_unchanged(self):
        client = self._sdk_client()
        changed = asyncio.run(apply_permission_mode(client, DEFAULT_PERMISSION_MODE))
        assert changed is False
        client.set_permission_mode.assert_not_awaited()

    def test_rejects_unknown_mode(self):
        client = self._sdk_client()
        changed = asyncio.run(apply_permission_mode(client, "dontAsk"))
        assert changed is False
        client.set_permission_mode.assert_not_awaited()

    def test_notifies_websocket_on_change(self):
        client = self._sdk_client()
        notifier = MagicMock()
        set_permission_mode_notifier(notifier)
        asyncio.run(apply_permission_mode(client, "acceptEdits"))
        notifier.write_message.assert_called_once()
        payload = notifier.write_message.call_args.args[0]
        # A real mode switch is not a reset: the UI applies it unconditionally.
        assert payload["data"] == {"mode": "acceptEdits", "reset": False}


class TestResetPermissionModeTracking:
    def test_reset_realigns_tracking_and_notifies_default(self):
        claude_module._current_permission_mode = "plan"
        notifier = MagicMock()
        set_permission_mode_notifier(notifier)
        reset_permission_mode_tracking()
        assert claude_module.get_current_permission_mode() == DEFAULT_PERMISSION_MODE
        payload = notifier.write_message.call_args.args[0]
        # Flagged reset so the UI only retires bypass and keeps an explicit
        # non-bypass selection (issue #377).
        assert payload["data"] == {"mode": DEFAULT_PERMISSION_MODE, "reset": True}

    def test_reset_notification_honors_managed_default(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"defaultMode": "plan"}})
        )
        notifier = MagicMock()
        set_permission_mode_notifier(notifier)
        reset_permission_mode_tracking()
        # Tracking reflects the SDK's actual state (a fresh client starts in
        # default); the notification carries the selector's starting value.
        assert claude_module.get_current_permission_mode() == DEFAULT_PERMISSION_MODE
        payload = notifier.write_message.call_args.args[0]
        assert payload["data"] == {"mode": "plan", "reset": True}

    def test_managed_bypass_default_never_arms_on_reset(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps(
                {"permissions": {"defaultMode": CLAUDE_BYPASS_PERMISSION_MODE}}
            )
        )
        notifier = MagicMock()
        set_permission_mode_notifier(notifier)
        reset_permission_mode_tracking()
        payload = notifier.write_message.call_args.args[0]
        assert payload["data"] == {"mode": DEFAULT_PERMISSION_MODE, "reset": True}


class TestBypassViaHandler:
    """Bypass is realized in NBI's handler, not the CLI's bypass mode (#377).

    The CLI refuses ``set_permission_mode("bypassPermissions")`` unless the
    session was launched with ``--dangerously-skip-permissions``, and that flag
    skips every permission prompt for the whole session (it can't be scoped to
    just the bypass turns). So bypass keeps the CLI in ``default`` and the
    permission handler auto-allows every tool while the tracked mode is bypass.
    """

    def _sdk_client(self):
        client = MagicMock()
        client.set_permission_mode = AsyncMock()
        return client

    def test_bypass_keeps_cli_in_default_but_tracks_bypass(self):
        client = self._sdk_client()
        notifier = MagicMock()
        set_permission_mode_notifier(notifier)
        changed = asyncio.run(
            apply_permission_mode(client, CLAUDE_BYPASS_PERMISSION_MODE)
        )
        assert changed is True
        # The CLI never enters bypassPermissions; it stays in default while NBI
        # tracks bypass for the handler and tells the UI bypass is active.
        client.set_permission_mode.assert_awaited_once_with(DEFAULT_PERMISSION_MODE)
        assert (
            claude_module.get_current_permission_mode()
            == CLAUDE_BYPASS_PERMISSION_MODE
        )
        payload = notifier.write_message.call_args.args[0]
        assert payload["data"] == {
            "mode": CLAUDE_BYPASS_PERMISSION_MODE,
            "reset": False,
        }

    def test_non_bypass_modes_pass_through_to_cli(self):
        client = self._sdk_client()
        asyncio.run(apply_permission_mode(client, "plan"))
        client.set_permission_mode.assert_awaited_once_with("plan")
        assert claude_module.get_current_permission_mode() == "plan"

    def test_reapplying_bypass_is_a_noop(self):
        # Already in bypass: no redundant CLI call, no re-notify.
        claude_module._current_permission_mode = CLAUDE_BYPASS_PERMISSION_MODE
        client = self._sdk_client()
        changed = asyncio.run(
            apply_permission_mode(client, CLAUDE_BYPASS_PERMISSION_MODE)
        )
        assert changed is False
        client.set_permission_mode.assert_not_awaited()

    def test_handler_auto_allows_every_tool_in_bypass(self):
        # Even a destructive command is allowed without a confirmation round
        # trip while bypass is the tracked mode.
        claude_module._current_permission_mode = CLAUDE_BYPASS_PERMISSION_MODE
        result = asyncio.run(
            custom_permission_handler("Bash", {"command": "rm -rf /tmp/x"}, {})
        )
        assert isinstance(result, PermissionResultAllow)


class TestPolicyWiring:
    def _nbi_config(self):
        config = MagicMock()
        config.claude_settings = {}
        config.enable_explain_error = False
        config.enable_output_followup = False
        config.enable_output_toolbar = False
        config.store_github_access_token = False
        config.refresh_open_files_on_disk_change = True
        return config

    def test_bypass_policy_defaults_to_force_off(self):
        assert (
            FEATURE_POLICY_DEFAULTS["claude_bypass_permissions"] == POLICY_FORCE_OFF
        )
        response = _build_feature_policies_response({}, self._nbi_config())
        assert response["claude_bypass_permissions"] == {
            "enabled": False,
            "locked": True,
        }

    def test_bypass_policy_user_choice_offers_option(self):
        response = _build_feature_policies_response(
            {"claude_bypass_permissions": POLICY_USER_CHOICE}, self._nbi_config()
        )
        assert response["claude_bypass_permissions"]["enabled"] is True

    def test_managed_settings_override_policy(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"disableBypassPermissionsMode": True}})
        )
        response = _build_feature_policies_response(
            {"claude_bypass_permissions": POLICY_USER_CHOICE}, self._nbi_config()
        )
        assert response["claude_bypass_permissions"] == {
            "enabled": False,
            "locked": True,
        }

    def test_default_permission_mode_from_managed_settings(
        self, _no_managed_settings
    ):
        _no_managed_settings.write_text(
            json.dumps({"permissions": {"defaultMode": "acceptEdits"}})
        )
        assert _resolve_default_permission_mode() == "acceptEdits"

    def test_default_permission_mode_never_starts_armed(self, _no_managed_settings):
        _no_managed_settings.write_text(
            json.dumps(
                {"permissions": {"defaultMode": CLAUDE_BYPASS_PERMISSION_MODE}}
            )
        )
        assert _resolve_default_permission_mode() == DEFAULT_PERMISSION_MODE

    def test_default_permission_mode_unmanaged(self):
        assert _resolve_default_permission_mode() == DEFAULT_PERMISSION_MODE

    def test_exposed_modes_are_the_contract(self):
        assert CLAUDE_PERMISSION_MODES == (
            "default",
            "acceptEdits",
            "plan",
            "bypassPermissions",
        )
