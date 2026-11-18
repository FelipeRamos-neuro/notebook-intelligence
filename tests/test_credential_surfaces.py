# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Credentials NBI stores must not reach the browser, the workspace, the
agent's file tools, or shell output in clear text."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import notebook_intelligence.built_in_toolsets as toolsets
from notebook_intelligence import extension as ext_module
from notebook_intelligence import util as util_mod
from notebook_intelligence.claude_mcp_manager import ClaudeMCPServer
from notebook_intelligence.credential_mask import (
    SECRET_MASK,
    collect_stored_secrets,
    mask_mcp_config,
    restore_masked_mcp_config,
)
from notebook_intelligence.util import (
    is_protected_nbi_path,
    redact_env_secrets,
    set_stored_secret_provider,
)

MCP = {
    "mcpServers": {
        "remote": {
            "url": "https://mcp.example",
            "headers": {"Authorization": "Bearer tok-abcdef123456", "X-Tenant": "acme"},
        },
        "local": {
            "command": "uvx",
            "env": {"GITHUB_TOKEN": "ghp-local-secret-1", "LOG_LEVEL": "verbose-debug"},
        },
    }
}


class TestClaudeMcpWire:
    def _server(self):
        return ClaudeMCPServer(
            name="x",
            scope="user",
            transport="http",
            url="https://m",
            headers={"Authorization": "Bearer abc123", "X-Tenant": "acme"},
            env={"API_KEY": "k-secret-value"},
        )

    def test_wire_dict_masks_every_env_and_header_value(self):
        wire = self._server().to_wire_dict()
        assert wire["headers"] == {"Authorization": SECRET_MASK, "X-Tenant": SECRET_MASK}
        assert wire["env"] == {"API_KEY": SECRET_MASK}
        assert wire["url"] == "https://m"
        assert "abc123" not in json.dumps(wire)

    def test_internal_dict_keeps_the_values(self):
        assert self._server().to_dict()["headers"]["Authorization"] == "Bearer abc123"


class TestMcpEditorMask:
    def test_only_credential_named_values_are_masked(self):
        masked = mask_mcp_config(MCP)["mcpServers"]
        assert masked["remote"]["headers"] == {
            "Authorization": SECRET_MASK,
            "X-Tenant": "acme",
        }
        assert masked["local"]["env"] == {
            "GITHUB_TOKEN": SECRET_MASK,
            "LOG_LEVEL": "verbose-debug",
        }
        assert MCP["mcpServers"]["local"]["env"]["GITHUB_TOKEN"] == "ghp-local-secret-1"

    def test_masked_document_round_trips_to_the_original(self):
        assert restore_masked_mcp_config(mask_mcp_config(MCP), MCP) == MCP

    def test_an_edited_value_replaces_the_stored_one(self):
        posted = mask_mcp_config(MCP)
        posted["mcpServers"]["local"]["env"]["GITHUB_TOKEN"] = "ghp-new-value-22"
        restored = restore_masked_mcp_config(posted, MCP)
        assert restored["mcpServers"]["local"]["env"]["GITHUB_TOKEN"] == "ghp-new-value-22"

    def test_a_mask_with_nothing_stored_is_refused(self):
        posted = mask_mcp_config(MCP)
        posted["mcpServers"]["renamed"] = posted["mcpServers"].pop("remote")
        with pytest.raises(ValueError, match=r"renamed\.headers\.Authorization"):
            restore_masked_mcp_config(posted, MCP)

    def test_non_server_documents_pass_through(self):
        assert mask_mcp_config({}) == {}
        assert restore_masked_mcp_config({"mcpServers": {}}, None) == {"mcpServers": {}}


class FakeConfig:
    def __init__(self, mcp):
        self.mcp = mcp
        self.user_mcp = None
        self.saved = False

    def load(self):
        pass

    def save(self):
        self.saved = True


def _handler():
    handler = MagicMock(spec=ext_module.MCPConfigFileHandler)
    handler.request = MagicMock()
    return handler


class TestMcpConfigHandler:
    @pytest.fixture
    def config(self, monkeypatch):
        config = FakeConfig(json.loads(json.dumps(MCP)))
        manager = SimpleNamespace(
            nbi_config=config,
            get_mcp_stdio_command_allowlist=lambda: [],
            update_mcp_servers=lambda: None,
        )
        monkeypatch.setattr(ext_module, "ai_service_manager", manager)
        return config

    def test_get_sends_no_credential(self, config):
        handler = _handler()
        ext_module.MCPConfigFileHandler.get.__wrapped__(handler)
        body = handler.finish.call_args[0][0]
        assert "tok-abcdef123456" not in body
        assert "ghp-local-secret-1" not in body
        assert "acme" in body

    def test_saving_the_masked_document_keeps_the_stored_values(self, config):
        handler = _handler()
        handler.request.body = json.dumps(mask_mcp_config(MCP)).encode()
        ext_module.MCPConfigFileHandler.post.__wrapped__(handler)
        assert config.saved
        assert config.user_mcp == MCP

    def test_an_unresolvable_mask_is_a_400_and_nothing_is_saved(self, config):
        posted = mask_mcp_config(MCP)
        posted["mcpServers"]["renamed"] = posted["mcpServers"].pop("remote")
        handler = _handler()
        handler.request.body = json.dumps(posted).encode()
        ext_module.MCPConfigFileHandler.post.__wrapped__(handler)
        handler.set_status.assert_called_with(400)
        assert "renamed.headers.Authorization" in handler.finish.call_args[0][0]
        assert not config.saved


@pytest.fixture
def home_root(tmp_path, monkeypatch):
    """A workspace that is the home directory, as when JupyterLab starts in ~."""
    monkeypatch.setenv("HOME", str(tmp_path))
    nbi = tmp_path / ".jupyter" / "nbi"
    nbi.mkdir(parents=True)
    (nbi / "config.json").write_text('{"claude_settings": {"api_key": "sk-REAL-9999"}}')
    (nbi / "mcp.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("api_key lives elsewhere\n")
    monkeypatch.setattr(util_mod, "_jupyter_root_dir", str(tmp_path))
    return tmp_path


def _run(tool, **kwargs):
    return asyncio.run(tool._tool_function(**kwargs))


class TestFileToolsRefuseNbiConfig:
    def test_read_file_refuses_config_and_still_reads_others(self, home_root):
        result = _run(toolsets.read_file, file_path=".jupyter/nbi/config.json")
        assert result == toolsets.PROTECTED_NBI_FILE_MESSAGE
        assert "REAL" not in result
        assert "elsewhere" in _run(toolsets.read_file, file_path="notes.txt")

    def test_write_tools_refuse_config(self, home_root):
        target = ".jupyter/nbi/mcp.json"
        assert _run(toolsets.write_to_file, file_path=target, content="x") == (
            toolsets.PROTECTED_NBI_FILE_MESSAGE
        )
        assert _run(
            toolsets.insert_content, file_path=target, line_number=1, content="x"
        ) == toolsets.PROTECTED_NBI_FILE_MESSAGE
        assert (home_root / ".jupyter/nbi/mcp.json").read_text() == "{}"

    def test_search_skips_config(self, home_root):
        result = _run(
            toolsets.search_files,
            pattern="**/*",
            directory=".",
            content_pattern="api_key",
        )
        assert "REAL" not in result
        assert "notes.txt" in result

    def test_a_symlink_to_config_is_refused(self, home_root):
        (home_root / "link.json").symlink_to(home_root / ".jupyter/nbi/config.json")
        assert _run(toolsets.read_file, file_path="link.json") == (
            toolsets.PROTECTED_NBI_FILE_MESSAGE
        )

    def test_editor_temp_copy_is_refused_wherever_it_is(self, home_root):
        (home_root / "nbi.mcp.temp.json").write_text("{}")
        assert is_protected_nbi_path(home_root / "nbi.mcp.temp.json")
        assert not is_protected_nbi_path(home_root / "notes.txt")


class TestStoredSecretRedaction:
    @pytest.fixture(autouse=True)
    def _clear(self):
        yield
        set_stored_secret_provider(None)

    def test_collects_keys_headers_and_secret_env_values(self):
        values = collect_stored_secrets(
            [
                {
                    "claude_settings": {"api_key": "sk-claude-1234"},
                    "acp_settings": {"api_key": "acp-key-12345"},
                    "chat_model": {
                        "properties": [
                            {"id": "api_key", "value": "proxy-key-98765"},
                            {"id": "base_url", "value": "https://proxy.example"},
                        ]
                    },
                }
            ],
            MCP,
        )
        assert {
            "sk-claude-1234",
            "acp-key-12345",
            "proxy-key-98765",
            "Bearer tok-abcdef123456",
            "tok-abcdef123456",
            "ghp-local-secret-1",
        } <= set(values)
        assert "https://proxy.example" not in values
        assert "acme" not in values and "verbose-debug" not in values

    def test_shell_output_loses_a_key_without_a_known_prefix(self):
        text = '{"api_key": "litellm-master-key-9f8e7d6c"}'
        assert "litellm-master-key" in redact_env_secrets(text)
        set_stored_secret_provider(lambda: ["litellm-master-key-9f8e7d6c"])
        assert "litellm-master-key" not in redact_env_secrets(text)

    def test_a_failing_provider_does_not_break_redaction(self):
        def boom():
            raise RuntimeError("config unreadable")

        set_stored_secret_provider(boom)
        assert redact_env_secrets("hello") == "hello"
