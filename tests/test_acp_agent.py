# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Unit tests for the ACP backend mapping (issue #378, Phase 1).

These exercise the editor-side translation (ACP events -> NBI cards/approval)
without launching codex-acp; the live end-to-end path is covered by the
Phase 0 spike and the JupyterLab Playwright check.
"""

import asyncio
import concurrent.futures
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from acp import schema

from notebook_intelligence.acp_agent import (
    _NbiAcpClient,
    _diffs_from_content,
    _nbi_kind,
    _nbi_status,
)
from notebook_intelligence.api import ChatResponse, ResponseStreamDataType


class FakeResponse(ChatResponse):
    """Captures streamed data and supports the user-input signal round trip."""

    def __init__(self):
        super().__init__()
        self.streamed = []

    @property
    def message_id(self) -> str:
        return "msg-1"

    def stream(self, data, finish: bool = False) -> None:
        self.streamed.append(data)

    def finish(self) -> None:
        pass


def _client_with_response(resp):
    owner = SimpleNamespace(
        current_response=resp,
        agent_spec=SimpleNamespace(label="Codex"),
    )
    return _NbiAcpClient(owner)


class TestKindStatusMapping:
    @pytest.mark.parametrize("acp_kind,expected", [
        ("read", "read"), ("search", "read"), ("fetch", "read"),
        ("edit", "edit"), ("delete", "edit"), ("move", "edit"),
        ("execute", "execute"), ("think", "other"), (None, "other"),
    ])
    def test_kind(self, acp_kind, expected):
        assert _nbi_kind(acp_kind) == expected

    @pytest.mark.parametrize("acp_status,expected", [
        ("pending", "in_progress"), ("in_progress", "in_progress"),
        (None, "in_progress"), ("completed", "completed"), ("failed", "failed"),
    ])
    def test_status(self, acp_status, expected):
        assert _nbi_status(acp_status) == expected


class TestDiffMapping:
    def test_file_edit_content_becomes_typed_diff_lines(self):
        content = [SimpleNamespace(type="diff", path="/x.py", old_text="a\n", new_text="a\nb\n")]
        diffs = _diffs_from_content(content)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "/x.py"
        assert {"type": "add", "content": "b"} in diffs[0]["lines"]

    def test_non_diff_content_ignored(self):
        content = [SimpleNamespace(type="content", text="hello")]
        assert _diffs_from_content(content) == []


class TestToolCallStreaming:
    def test_tool_call_emits_card_with_kind_and_diff(self):
        resp = FakeResponse()
        client = _client_with_response(resp)
        update = SimpleNamespace(
            session_update="tool_call", tool_call_id="t1", kind="edit",
            status="in_progress", title="Edit /x.py",
            content=[SimpleNamespace(type="diff", path="/x.py", old_text="", new_text="hi\n")],
        )
        asyncio.run(client.session_update("s", update))
        cards = [d for d in resp.streamed if d.data_type == ResponseStreamDataType.ToolCall]
        assert len(cards) == 1
        assert cards[0].id == "t1" and cards[0].kind == "edit"
        assert cards[0].status == "in_progress" and cards[0].diffs

    def test_partial_update_merges_cached_kind(self):
        resp = FakeResponse()
        client = _client_with_response(resp)
        asyncio.run(client.session_update("s", SimpleNamespace(
            session_update="tool_call", tool_call_id="t1", kind="execute",
            status="in_progress", title="Run", content=None)))
        # A later update carries only the new status; kind must survive.
        asyncio.run(client.session_update("s", SimpleNamespace(
            session_update="tool_call_update", tool_call_id="t1", kind=None,
            status="completed", title=None, content=None)))
        last = [d for d in resp.streamed if d.data_type == ResponseStreamDataType.ToolCall][-1]
        assert last.kind == "execute" and last.status == "completed"

    def test_agent_message_chunk_streams_markdown_part(self):
        # MarkdownPart, not Markdown: ACP delivers token-sized deltas and the
        # frontend only concatenates consecutive *parts* into one block. With
        # Markdown every delta rendered as its own paragraph (the one-word-
        # per-line bug from the PR #380 review).
        resp = FakeResponse()
        client = _client_with_response(resp)
        asyncio.run(client.session_update("s", SimpleNamespace(
            session_update="agent_message_chunk",
            content=SimpleNamespace(text="hello world"))))
        md = [d for d in resp.streamed if d.data_type == ResponseStreamDataType.MarkdownPart]
        assert md and md[0].content == "hello world"

    def test_agent_thought_chunk_streams_reasoning_part(self):
        resp = FakeResponse()
        client = _client_with_response(resp)
        asyncio.run(client.session_update("s", SimpleNamespace(
            session_update="agent_thought_chunk",
            content=SimpleNamespace(text="mulling"))))
        md = [d for d in resp.streamed if d.data_type == ResponseStreamDataType.MarkdownPart]
        assert md and md[0].reasoning_content == "mulling"


class TestPermission:
    def _opts(self):
        return [
            schema.PermissionOption(kind="allow_once", name="Allow", option_id="a1"),
            schema.PermissionOption(kind="reject_once", name="Reject", option_id="r1"),
        ]

    def _tool_call(self):
        return SimpleNamespace(tool_call_id="t1", title="Run echo")

    def _run_with_answer(self, confirmed):
        resp = FakeResponse()
        client = _client_with_response(resp)

        async def drive():
            task = asyncio.create_task(
                client.request_permission(self._opts(), "s", self._tool_call())
            )
            # Let request_permission stream the card and start awaiting input.
            await asyncio.sleep(0.05)
            assert any(
                d.data_type == ResponseStreamDataType.Confirmation for d in resp.streamed
            )
            resp.on_user_input({
                "callback_id": "acp-perm-t1", "data": {"confirmed": confirmed}
            })
            return await task

        return asyncio.run(drive())

    def test_approve_selects_allow_option(self):
        result = self._run_with_answer(True)
        assert isinstance(result.outcome, schema.AllowedOutcome)
        assert result.outcome.option_id == "a1"

    def test_reject_selects_reject_option(self):
        result = self._run_with_answer(False)
        assert isinstance(result.outcome, schema.AllowedOutcome)
        assert result.outcome.option_id == "r1"

    def test_no_response_fails_closed(self):
        client = _client_with_response(None)
        result = asyncio.run(
            client.request_permission(self._opts(), "s", self._tool_call())
        )
        assert isinstance(result.outcome, schema.DeniedOutcome)


class TestPolicyClamp:
    def test_force_off_clamps_enabled(self):
        from notebook_intelligence.feature_flags import apply_acp_policies
        assert apply_acp_policies({"enabled": True}, {"acp_mode": "force-off"}) == {"enabled": False}

    def test_user_choice_keeps_user_value(self):
        from notebook_intelligence.feature_flags import apply_acp_policies
        assert apply_acp_policies({"enabled": True}, {"acp_mode": "user-choice"}) == {"enabled": True}

    def test_full_access_force_off_clamps(self):
        from notebook_intelligence.feature_flags import apply_acp_policies
        out = apply_acp_policies(
            {"full_access": True}, {"acp_full_access": "force-off"}
        )
        assert out["full_access"] is False

    def test_full_access_user_choice_keeps_value(self):
        from notebook_intelligence.feature_flags import apply_acp_policies
        assert (
            apply_acp_policies(
                {"full_access": True}, {"acp_full_access": "user-choice"}
            )["full_access"]
            is True
        )

    def test_full_access_force_on(self):
        from notebook_intelligence.feature_flags import apply_acp_policies
        assert (
            apply_acp_policies(
                {"full_access": False}, {"acp_full_access": "force-on"}
            )["full_access"]
            is True
        )


class TestApprovalArgs:
    """The approval posture pinned onto the codex-acp command line."""

    def test_default_pins_untrusted(self):
        from notebook_intelligence.acp_agent import codex_approval_args
        args = codex_approval_args(False)
        assert args == ["-c", 'approval_policy="untrusted"']

    def test_full_access_runs_unattended_with_a_writable_workspace(self):
        """Full access must pin a writable sandbox, not only stop asking.

        Codex's default sandbox for a project with no trust entry is read-only. With
        ``approval_policy = never`` nothing can be approved past it, so a full
        access session with only the approval flag refused every edit
        ("patch rejected: writing is blocked by read-only sandbox").
        """
        from notebook_intelligence.acp_agent import codex_approval_args
        assert codex_approval_args(True) == [
            "-c", 'approval_policy="never"',
            "-c", 'sandbox_mode="workspace-write"',
        ]


def _captured_launch_cmd(nbi_config):
    """Run ``AcpAgentClient._serve`` just far enough to capture its argv."""
    import notebook_intelligence.acp_agent as mod

    host = SimpleNamespace(websocket_connector=None, nbi_config=nbi_config)
    client = mod.AcpAgentClient(host)
    captured = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        raise RuntimeError("test: argv captured, subprocess intentionally not started")

    orig = mod.asyncio.create_subprocess_exec
    mod.asyncio.create_subprocess_exec = fake_exec
    try:
        asyncio.run(client._serve())
    finally:
        mod.asyncio.create_subprocess_exec = orig
    assert "cmd" in captured, "_serve returned before launching the adapter"
    return captured["cmd"]


class TestCodexModelArgs:
    """Model and base-URL settings pinned onto the codex-acp command line.

    Codex ignores the OPENAI_BASE_URL env var, so the -c openai_base_url
    override is the only path that gets a custom endpoint to the agent
    (the PR #380 regression: a proxy user's key was sent to api.openai.com
    and 401ed).
    """

    def test_empty_settings_add_nothing(self):
        from notebook_intelligence.acp_registry import codex_model_args
        assert codex_model_args({}) == []
        assert codex_model_args({"chat_model": "", "base_url": "  "}) == []

    def test_base_url_becomes_openai_base_url_override(self):
        from notebook_intelligence.acp_registry import codex_model_args
        args = codex_model_args({"base_url": "http://127.0.0.1:8901/v1"})
        assert args == ["-c", 'openai_base_url="http://127.0.0.1:8901/v1"']

    def test_chat_model_becomes_model_override(self):
        from notebook_intelligence.acp_registry import codex_model_args
        assert codex_model_args({"chat_model": "gpt-5.2-codex"}) == [
            "-c", 'model="gpt-5.2-codex"'
        ]

    def test_both_settings_yield_both_overrides(self):
        from notebook_intelligence.acp_registry import codex_model_args
        args = codex_model_args(
            {"chat_model": "gpt-5.2-codex", "base_url": "https://llm.corp/v1"}
        )
        assert args == [
            "-c", 'model="gpt-5.2-codex"',
            "-c", 'openai_base_url="https://llm.corp/v1"',
        ]

    def test_values_are_quoted_as_toml_strings(self):
        from notebook_intelligence.acp_registry import codex_model_args
        args = codex_model_args({"base_url": 'https://x/v1?a="b"\\c'})
        assert args == ["-c", 'openai_base_url="https://x/v1?a=\\"b\\"\\\\c"']

    def test_control_chars_are_dropped(self):
        """Control chars cannot ride in a TOML basic string; an interior
        newline from a paste artifact must not break the codex launch."""
        from notebook_intelligence.acp_registry import codex_model_args
        args = codex_model_args({"base_url": "https://proxy\n.corp/v1\x01"})
        assert args == ["-c", 'openai_base_url="https://proxy.corp/v1"']

    def test_value_left_empty_by_cleaning_adds_no_flag(self):
        """An empty override is not neutral: it would blank out the model
        codex would otherwise take from its config file or default."""
        from notebook_intelligence.acp_registry import codex_model_args
        assert codex_model_args({"chat_model": "\x08", "base_url": "\x01\x02"}) == []

    def test_serve_appends_overrides_to_launch_cmd(self, tmp_path):
        """Pin the delivery, not just the mapping: the original bug was
        settings that never reached the launch command at all."""
        cmd = _captured_launch_cmd(SimpleNamespace(
            acp_settings={
                "enabled": True, "agent": "codex",
                "chat_model": "m1", "base_url": "http://proxy/v1",
                "full_access": False,
            },
            nbi_user_dir=str(tmp_path),
        ))

        assert cmd[-6:] == [
            "-c", 'approval_policy="untrusted"',
            "-c", 'model="m1"',
            "-c", 'openai_base_url="http://proxy/v1"',
        ]


class TestCodexKeyNotPersisted:
    """When NBI supplies Codex's API key, Codex does not write it to disk or
    pass it to the commands it runs. By default codex-acp wrote it to
    CODEX_HOME/auth.json and kept using that saved key after it changed,
    Codex's shell snapshots wrote it to disk, and every command Codex ran
    could read it."""

    AUTH_ARGS = [
        "-c", 'cli_auth_credentials_store="ephemeral"',
        "-c", "features.shell_snapshot=false",
        "-c", 'shell_environment_policy.exclude=["OPENAI_API_KEY"]',
    ]

    @pytest.fixture(autouse=True)
    def _default_adapter_command(self, monkeypatch):
        monkeypatch.delenv("NBI_ACP_AGENT_COMMAND", raising=False)

    def _launch(self, tmp_path, acp_settings):
        import notebook_intelligence.acp_agent as mod

        host = SimpleNamespace(
            websocket_connector=None,
            nbi_config=SimpleNamespace(
                acp_settings={"enabled": True, "agent": "codex", "full_access": False, **acp_settings},
                nbi_user_dir=str(tmp_path),
            ),
        )
        client = mod.AcpAgentClient(host)
        captured = {}

        async def fake_exec(*cmd, **kw):
            captured["cmd"] = list(cmd)
            captured["env"] = kw.get("env", {})
            raise RuntimeError("captured; abort launch")

        orig = mod.asyncio.create_subprocess_exec
        mod.asyncio.create_subprocess_exec = fake_exec
        try:
            asyncio.run(client._serve())
        finally:
            mod.asyncio.create_subprocess_exec = orig
        return captured

    def test_auth_args_only_when_nbi_supplies_the_key(self):
        from notebook_intelligence.acp_registry import codex_auth_args
        assert codex_auth_args("OPENAI_API_KEY") == self.AUTH_ARGS
        assert codex_auth_args("") == []

    @pytest.mark.parametrize("settings_key,env_key,supplied", [
        ("sk-settings", None, True),
        ("", "sk-env", True),
        ("   ", None, False),
        ("", "   ", False),
        ("", None, False),
    ])
    def test_args_come_exactly_with_the_isolated_codex_home(
        self, tmp_path, monkeypatch, settings_key, env_key, supplied
    ):
        if env_key is None:
            monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        else:
            monkeypatch.setenv("OPENAI_API_KEY", env_key)
        captured = self._launch(tmp_path, {"api_key": settings_key})
        codex_home = str(tmp_path / "codex-home")
        assert (captured["cmd"][3:9] == self.AUTH_ARGS) is supplied
        assert (captured["env"].get("CODEX_HOME") == codex_home) is supplied

    def test_auth_args_come_before_the_approval_pin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        captured = self._launch(tmp_path, {})
        assert captured["cmd"][3:9] == self.AUTH_ARGS
        assert captured["cmd"][9:11] == ["-c", 'approval_policy="untrusted"']

    def test_saved_key_copies_left_by_earlier_versions_are_removed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        codex_home = tmp_path / "codex-home"
        (codex_home / "shell_snapshots").mkdir(parents=True)
        (codex_home / "auth.json").write_text('{"OPENAI_API_KEY": "sk-old"}')
        (codex_home / "shell_snapshots" / "a.sh").write_text("export OPENAI_API_KEY=sk-old")
        (codex_home / "shell_snapshots" / "b.ps1").write_text("$env:OPENAI_API_KEY='sk-old'")
        (codex_home / "config.toml").write_text("")
        self._launch(tmp_path, {"api_key": "sk-new"})
        assert not (codex_home / "auth.json").exists()
        assert list((codex_home / "shell_snapshots").iterdir()) == []
        assert (codex_home / "config.toml").exists()

    def test_old_copies_are_removed_even_without_a_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        codex_home = tmp_path / "codex-home"
        (codex_home / "shell_snapshots").mkdir(parents=True)
        (codex_home / "shell_snapshots" / "a.sh").write_text("export OPENAI_API_KEY=sk-old")
        captured = self._launch(tmp_path, {})
        assert list((codex_home / "shell_snapshots").iterdir()) == []
        assert "cli_auth_credentials_store" not in " ".join(captured["cmd"])

    def test_cleanup_does_not_follow_symlinks(self, tmp_path):
        from notebook_intelligence.acp_agent import _remove_persisted_codex_credentials
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "important.txt").write_text("keep")
        (outside / "auth.json").write_text("keep")
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        (codex_home / "shell_snapshots").symlink_to(outside, target_is_directory=True)
        _remove_persisted_codex_credentials(str(codex_home))
        assert (outside / "important.txt").exists()

        linked_home = tmp_path / "linked-home"
        linked_home.symlink_to(outside, target_is_directory=True)
        _remove_persisted_codex_credentials(str(linked_home))
        assert (outside / "auth.json").exists()

    def test_symlinks_inside_the_snapshot_directory_are_removed_as_links(self, tmp_path):
        from notebook_intelligence.acp_agent import _remove_persisted_codex_credentials
        target = tmp_path / "target.sh"
        target.write_text("keep")
        snapshots = tmp_path / "codex-home" / "shell_snapshots"
        snapshots.mkdir(parents=True)
        (snapshots / "link.sh").symlink_to(target)
        (snapshots / "nested").mkdir()
        (snapshots / "nested" / "inner.sh").write_text("left alone")
        _remove_persisted_codex_credentials(str(tmp_path / "codex-home"))
        assert target.read_text() == "keep"
        assert not (snapshots / "link.sh").is_symlink()
        assert (snapshots / "nested" / "inner.sh").exists()

    def test_an_unreadable_snapshot_directory_does_not_stop_the_launch(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        codex_home = tmp_path / "codex-home"
        snapshots = codex_home / "shell_snapshots"
        snapshots.mkdir(parents=True)
        (codex_home / "auth.json").write_text("{}")
        snapshots.chmod(0)
        try:
            if os.access(snapshots, os.R_OK):
                pytest.skip("running with permissions that ignore the mode")
            captured = self._launch(tmp_path, {})
        finally:
            snapshots.chmod(0o755)
        assert captured["cmd"][:3] == ["npx", "-y", "@zed-industries/codex-acp@0.16.0"]
        assert not (codex_home / "auth.json").exists()
        assert "Could not list old Codex shell snapshots" in caplog.text

    def test_a_linked_codex_home_is_not_cleaned(self, tmp_path, caplog):
        from notebook_intelligence.acp_agent import _remove_persisted_codex_credentials
        real = tmp_path / "real-codex"
        real.mkdir()
        (real / "auth.json").write_text("keep")
        (tmp_path / "codex-home").symlink_to(real, target_is_directory=True)
        _remove_persisted_codex_credentials(str(tmp_path / "codex-home"))
        assert (real / "auth.json").exists()
        assert "it is a link" in caplog.text

    def test_a_file_that_cannot_be_removed_is_logged(self, tmp_path, caplog):
        from notebook_intelligence.acp_agent import _remove_persisted_codex_credentials
        (tmp_path / "auth.json").mkdir()
        _remove_persisted_codex_credentials(str(tmp_path))
        assert "Could not remove an old Codex key copy" in caplog.text

    def test_nothing_is_logged_when_there_is_nothing_to_remove(self, tmp_path, caplog):
        from notebook_intelligence.acp_agent import _remove_persisted_codex_credentials
        (tmp_path / "shell_snapshots").mkdir()
        _remove_persisted_codex_credentials(str(tmp_path))
        _remove_persisted_codex_credentials(str(tmp_path / "missing"))
        assert caplog.records == []


class TestFullAccessLaunch:
    """Full access reaches the launch command as a writable sandbox, and only
    when the effective, policy-clamped setting allows it."""

    _FULL_ACCESS_PINS = [
        "-c", 'approval_policy="never"',
        "-c", 'sandbox_mode="workspace-write"',
    ]

    @pytest.fixture(autouse=True)
    def _default_adapter_command(self, monkeypatch):
        monkeypatch.delenv("NBI_ACP_AGENT_COMMAND", raising=False)

    def test_missing_full_access_setting_means_off(self, tmp_path):
        """Under ``user-choice`` nothing writes the key, so a user who never
        touched the toggle has no ``full_access`` stored at all."""
        cmd = _captured_launch_cmd(SimpleNamespace(
            acp_settings={"enabled": True, "agent": "codex"},
            nbi_user_dir=str(tmp_path),
        ))
        assert cmd[-2:] == ["-c", 'approval_policy="untrusted"']
        assert not any("sandbox_mode" in arg for arg in cmd)

    def test_full_access_launches_with_a_writable_sandbox(self, tmp_path):
        cmd = _captured_launch_cmd(SimpleNamespace(
            acp_settings={"enabled": True, "agent": "codex", "full_access": True},
            nbi_user_dir=str(tmp_path),
        ))
        assert cmd[-4:] == self._FULL_ACCESS_PINS

    def test_force_off_keeps_a_stored_full_access_off(self, mock_nbi_config):
        """A stored ``full_access: true`` must not reach the launch command
        while the policy is force-off; it would now grant workspace writes."""
        mock_nbi_config.user_config["acp_settings"] = {
            "enabled": True, "agent": "codex", "full_access": True,
        }
        mock_nbi_config.set_feature_policies({"acp_full_access": "force-off"}, {})
        cmd = _captured_launch_cmd(mock_nbi_config)
        assert cmd[-2:] == ["-c", 'approval_policy="untrusted"']
        assert not any("sandbox_mode" in arg for arg in cmd)

    def test_force_on_turns_full_access_on(self, mock_nbi_config):
        mock_nbi_config.user_config["acp_settings"] = {
            "enabled": True, "agent": "codex", "full_access": False,
        }
        mock_nbi_config.set_feature_policies({"acp_full_access": "force-on"}, {})
        cmd = _captured_launch_cmd(mock_nbi_config)
        assert cmd[-4:] == self._FULL_ACCESS_PINS

    def test_agent_command_override_keeps_the_pins(self, tmp_path, monkeypatch):
        """An admin-pinned adapter binary must not lose the approval and
        sandbox posture."""
        monkeypatch.setenv("NBI_ACP_AGENT_COMMAND", "/opt/codex-acp --verbose")
        cmd = _captured_launch_cmd(SimpleNamespace(
            acp_settings={"enabled": True, "agent": "codex", "full_access": True},
            nbi_user_dir=str(tmp_path),
        ))
        assert cmd[:2] == ["/opt/codex-acp", "--verbose"]
        assert cmd[-4:] == self._FULL_ACCESS_PINS


class TestNbiMcpServerLaunch:
    """Codex starts NBI's MCP server outside its sandbox with the workspace as
    cwd, and full access lets the agent write that workspace, so nothing
    written there may run in place of the server."""

    @staticmethod
    def _server():
        import notebook_intelligence.acp_agent as mod

        client = mod.AcpAgentClient(SimpleNamespace(
            websocket_connector=None,
            nbi_config=SimpleNamespace(acp_settings={}, nbi_user_dir="/unused"),
        ))
        return client._mcp_servers()[0]

    def test_launches_by_absolute_file_path(self):
        server = self._server()
        assert "-m" not in server.args
        assert os.path.isabs(server.args[0])
        assert server.args[0].endswith(os.path.join("notebook_intelligence", "acp_mcp_server.py"))

    def test_a_package_planted_in_the_workspace_does_not_run(self, tmp_path):
        planted = tmp_path / "notebook_intelligence"
        planted.mkdir()
        (planted / "__init__.py").write_text("print('PLANTED PACKAGE RAN', flush=True)\n")
        (planted / "acp_mcp_server.py").write_text("print('PLANTED SERVER RAN', flush=True)\n")
        server = self._server()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        proc = subprocess.run(
            [server.command, *server.args],
            input=request + "\n", cwd=tmp_path,
            capture_output=True, text=True, timeout=30,
        )

        assert "PLANTED" not in proc.stdout + proc.stderr
        reply = json.loads(proc.stdout.splitlines()[0])
        assert reply["result"]["serverInfo"]["name"] == "nbi"


class TestAssembleQuery:
    """The turn's context lines (attachments, current-file pointer, output
    context) ride along with the prompt — sending only ``request.prompt``
    silently dropped whatever the user had just attached (the file-as-context
    bug from the PR #380 review)."""

    def _assemble(self, chat_history, prompt="the prompt"):
        from notebook_intelligence.acp_agent import AcpAgentClient
        return AcpAgentClient.assemble_query(
            SimpleNamespace(prompt=prompt, chat_history=chat_history)
        )

    def test_context_lines_precede_the_prompt(self):
        query = self._assemble([
            {"role": "user", "content": "The user attached @data.csv."},
            {"role": "user", "content": "what is in this file?"},
        ])
        assert query == "The user attached @data.csv.\nwhat is in this file?"

    def test_empty_history_falls_back_to_prompt(self):
        assert self._assemble([]) == "the prompt"

    def test_non_user_and_non_string_content_skipped(self):
        query = self._assemble([
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": [{"type": "text", "text": "structured"}]},
            {"role": "user", "content": "the prompt"},
        ])
        assert query == "the prompt"

    def test_control_slash_command_drops_context(self):
        # Context lines are meaningless to a control command and could break
        # its parsing; mirrors the Claude-mode join after #388.
        query = self._assemble([
            {"role": "user", "content": "The user attached @data.csv."},
            {"role": "user", "content": "/compact"},
        ])
        assert query == "/compact"

    def test_custom_slash_command_keeps_context_after_the_command(self):
        # A non-control command is hoisted to the front (the agent only
        # recognizes a command at the start of the prompt) with the turn's
        # context preserved as its arguments; mirrors Claude mode's #388 join.
        query = self._assemble([
            {"role": "user", "content": "The user attached @data.csv."},
            {"role": "user", "content": "/analyze"},
        ])
        assert query == "/analyze\nThe user attached @data.csv."

    def test_bare_custom_command_with_no_context_stays_clean(self):
        query = self._assemble(
            [{"role": "user", "content": "/analyze"}], prompt="/analyze"
        )
        assert query == "/analyze"


class TestStripContextPreamble:
    """Session previews should show the user's first question, not the NBI
    context lines the agent stored as part of its session title."""

    def test_strips_leading_context_lines(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w'\n"
            "The user attached @facts.md. Read it if relevant.\n"
            "What is the launch codename?"
        )
        assert _strip_context_preamble(title) == "What is the launch codename?"

    def test_plain_title_unchanged(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        assert _strip_context_preamble("say hi") == "say hi"

    def test_all_context_falls_back_to_original(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = "Additional context: Current directory open in Jupyter is: '/w'"
        assert _strip_context_preamble(title) == title

    def test_joined_form_strips_directory_pointer(self):
        # codex stores titles with newlines collapsed to spaces (and
        # truncated), so the pointer must be peeled off structurally.
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "Reply with two short sentences."
        )
        assert _strip_context_preamble(title) == "Reply with two short sentences."

    def test_joined_form_with_current_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb' What does this cell do?"
        )
        assert _strip_context_preamble(title) == "What does this cell do?"

    def test_joined_form_with_language_and_kernel(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb' "
            "and active programming language is: 'python' "
            "with active kernel name: 'python3' (Python 3 (ipykernel)) "
            "What does this cell do?"
        )
        assert _strip_context_preamble(title) == "What does this cell do?"

    def test_codex_title_cut_inside_the_pointer_names_the_file(self):
        # Verbatim session/list title from codex-acp 0.16.0: the pointer
        # fills its 117 characters, so none of the question survives.
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "and current file is: 'analysis.py' and active programmin..."
        )
        assert _strip_context_preamble(title) == "analysis.py"

    # The titles below are built from a pointer shaped like extension.py's
    # and truncated the way each agent truncates, so each is one the picker
    # can actually receive.

    def test_codex_title_names_the_file_relative_to_the_directory(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "notebooks", "notebooks/eda.ipynb", "python", "python3",
            "Python 3 (ipykernel)", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "eda.ipynb"

    def test_codex_title_cut_inside_the_file_name_shows_the_partial_name(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "reports", "reports/2026-q3-regional-summary.ipynb", "python",
            question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "2026-q3-regional-su..."

    def test_codex_title_cut_before_the_file_name_names_the_directory(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "projects/analytics", "projects/analytics/revenue.ipynb", "python",
            question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "projects/analytics"

    def test_codex_title_file_outside_the_directory_keeps_its_path(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "notebooks", "data/load.py", "python", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "data/load.py"

    def test_codex_title_file_named_like_the_start_of_the_directory_keeps_its_name(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "notes-archive", "notes", "python", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "notes"

    def test_codex_title_cut_before_any_file_names_the_directory(self):
        # No document focused (for example the launcher): the pointer has a
        # directory and a language but no file.
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "notebooks/experiments/2026", language="python",
            question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "notebooks/experiments/2026"

    def test_codex_title_cut_inside_the_file_label_names_the_directory(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "home/analyst/projects/2026/quarterly",
            "home/analyst/projects/2026/quarterly/a.ipynb", "python", "python3",
            "Python 3 (ipykernel)", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "home/analyst/projects/2026/quarterly"

    def test_codex_title_cut_inside_the_kernel_label_with_no_directory_is_empty(self):
        # Notebook generation sends no file and an empty directory.
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "", language="python", kernel="python3", display="Python 3 (ipykernel)",
            question="Create a notebook that plots revenue",
        ))
        assert _strip_context_preamble(title) == ""

    def test_codex_title_cut_inside_the_directory_is_empty(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "home/analyst/projects/2026/quarterly-revenue-review/regional",
            "home/analyst/projects/2026/quarterly-revenue-review/regional/a.ipynb",
            "python", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == ""

    def test_codex_title_keeps_a_truncated_question(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title(_prompt(
            "", language="python",
            question="Run analysis.py and check whether the revenue totals "
            "look right, then fix anything that is wrong",
        ))
        assert _strip_context_preamble(title) == "Run analysi..."

    def test_claude_code_acp_title_names_the_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _claude_code_acp_title(_prompt(
            "", "analysis.ipynb", "python", "python3", "Python 3 (ipykernel)",
            question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == "analysis.ipynb"

    def test_claude_code_acp_title_cut_inside_the_file_name(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _claude_code_acp_title(_prompt(
            "", "quarterly-revenue-review-by-region-and-product-line.ipynb",
            "python", question="Summarize the data",
        ))
        assert _strip_context_preamble(title) == (
            "quarterly-revenue-review-by-region-and-produ\u2026"
        )

    def test_hoisted_slash_command_before_the_pointer_is_kept(self):
        # assemble_query moves a custom command in front of the context lines.
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = _codex_title("/analyze\n" + _prompt(
            "", "analysis.py", "python", question="",
        ))
        assert _strip_context_preamble(title) == "/analyze"

    # Shapes the agents do not produce, pinning the matcher on its own.

    def test_cut_inside_a_quoted_value_names_the_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb' "
            "and active programming language is: 'pyt..."
        )
        assert _strip_context_preamble(title) == "nb.ipynb"

    def test_cut_at_a_segment_boundary_names_the_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb'..."
        )
        assert _strip_context_preamble(title) == "nb.ipynb"

    def test_cut_inside_the_kernel_display_name_names_the_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "and current file is: 'a.ipynb' "
            "with active kernel name: 'python3' (Python 3 (ipyk..."
        )
        assert _strip_context_preamble(title) == "a.ipynb"

    def test_question_ending_in_an_ellipsis_is_not_mistaken_for_a_cut(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "and current file is: 'a.py' Wait for it..."
        )
        assert _strip_context_preamble(title) == "Wait for it..."

    def test_segment_text_without_a_marker_is_the_question(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = "Additional context: Current directory open in Jupyter is: '' with"
        assert _strip_context_preamble(title) == "with"

    def test_parenthesized_question_without_a_kernel_is_kept(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "and current file is: 'a.py' (quick one) what does line 3 do?"
        )
        assert _strip_context_preamble(title) == "(quick one) what does line 3 do?"

    def test_truncated_parenthesized_question_without_a_kernel_is_kept(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '' "
            "and current file is: 'a.py' (quick one about the loop in..."
        )
        assert _strip_context_preamble(title) == "(quick one about the loop in..."

    def test_truncated_parenthesized_question_after_a_display_name_is_kept(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb' "
            "and active programming language is: 'python' "
            "with active kernel name: 'python3' (Python 3 (ipykernel)) "
            "(quick one about the loop in..."
        )
        assert _strip_context_preamble(title) == "(quick one about the loop in..."

    def test_untruncated_pointer_with_no_question_names_the_file(self):
        from notebook_intelligence.acp_agent import _strip_context_preamble
        title = (
            "Additional context: Current directory open in Jupyter is: '/w' "
            "and current file is: 'nb.ipynb'"
        )
        assert _strip_context_preamble(title) == "nb.ipynb"


def _prompt(directory, file="", language="", kernel="", display="", question=""):
    """A first prompt shaped like extension.py's: the pointer, then the question."""
    from notebook_intelligence.claude_sessions import NBI_CONTEXT_PREFIX
    pointer = f"{NBI_CONTEXT_PREFIX} '{directory}'"
    if file:
        pointer += f" and current file is: '{file}'"
    if language:
        pointer += f" and active programming language is: '{language}'"
    if kernel:
        pointer += f" with active kernel name: '{kernel}'"
    if display:
        pointer += f" ({display})"
    return f"{pointer}\n{question}" if question else pointer


def _codex_title(prompt):
    """codex-acp's session title for ASCII text (it counts graphemes): newlines
    as spaces, 117 characters plus "..."."""
    text = prompt.replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= 120 else text[:117] + "..."


def _claude_code_acp_title(prompt):
    """claude-code-acp's sanitizeTitle for ASCII text (it counts UTF-16 units):
    whitespace collapsed, 127 characters plus U+2026."""
    text = " ".join(prompt.split())
    return text if len(text) <= 128 else text[:127] + "\u2026"


class TestSingleFlight:
    """The ACP session runs one prompt at a time; a second concurrent turn
    must be rejected rather than interleave with the first."""

    def _client(self):
        from notebook_intelligence.acp_agent import AcpAgentClient
        host = SimpleNamespace(
            websocket_connector=None,
            nbi_config=SimpleNamespace(acp_settings={"enabled": True}),
        )
        return AcpAgentClient(host)

    def test_second_concurrent_turn_is_rejected(self):
        client = self._client()
        # Simulate a turn already in flight by holding the turn lock.
        assert client._turn_lock.acquire(blocking=False)
        try:
            req = SimpleNamespace(prompt="hi", cancel_token=None, chat_history=[])
            result = client.query(req, FakeResponse())
            assert result is not None and "busy" in result.lower()
        finally:
            client._turn_lock.release()

    def test_unavailable_agent_releases_the_lock(self):
        client = self._client()
        # When the agent can't start, query returns the error and still frees
        # the lock (the outer finally).
        client._ensure_started = lambda: False
        client._start_error = "boom"
        result = client.query(SimpleNamespace(prompt="x", cancel_token=None, chat_history=[]), FakeResponse())
        assert result == "boom"
        assert client._turn_lock.acquire(blocking=False)
        client._turn_lock.release()

    def test_completed_turn_releases_lock_and_resets_tool_state(self):
        client = self._client()
        # Drive query through the inner run/poll body to a clean finish so the
        # nested try/finally (lock release + current_response reset) is covered,
        # not just the early-return path.
        client._ensure_started = lambda: True
        client._loop = object()  # only used as an opaque handle below
        client._client = SimpleNamespace(
            _tool_state={"stale": {}}, _tool_perf_spans={}
        )

        done = concurrent.futures.Future()
        done.set_result(None)

        async def _noop():
            return None

        client._run_prompt = lambda prompt: _noop()

        def fake_schedule(coro, loop):
            coro.close()  # we never run the real prompt coroutine
            return done

        import notebook_intelligence.acp_agent as mod
        orig = mod.asyncio.run_coroutine_threadsafe
        mod.asyncio.run_coroutine_threadsafe = fake_schedule
        try:
            result = client.query(
                SimpleNamespace(prompt="hi", cancel_token=None, chat_history=[]),
                FakeResponse(),
            )
        finally:
            mod.asyncio.run_coroutine_threadsafe = orig

        assert result is None
        # Prior turn's tool-call cache was cleared, lock released, response reset.
        assert client._client._tool_state == {}
        assert client.current_response is None
        assert client._turn_lock.acquire(blocking=False)
        client._turn_lock.release()
