# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Issue #433: a malformed section in mcp.json must not break MCP setup.

`update_mcp_servers` defaulted with `mcp_config.get(key, {})`, which returns
the default only when the key is absent. An explicit `"mcpServers": null`
therefore yielded `None`, and the loader raised
`AttributeError: 'NoneType' object has no attribute 'keys'` at every session
start, leaving the user with no MCP servers and no error at the point they
caused it. `"participants": null` had the identical defect on the next line.

The shape validator (`validate_mcp_config`) rejects these values, but it is
wired only into the save path, so a file already on disk (hand-edited,
written by an older build, or produced by any other tool) never met it.

These drive the real `MCPManager.update_mcp_servers`. With no servers
configured the loader connects nothing, so the manager is constructible in a
unit test without stubbing the worker machinery.
"""

import logging

import pytest
from notebook_intelligence.mcp_manager import MCPManager, _config_section


class TestConfigSection:
    def test_returns_the_mapping_when_valid(self):
        section = {"my-server": {"command": "uvx"}}
        assert _config_section({"mcpServers": section}, "mcpServers") is section

    def test_absent_key_is_empty_and_silent(self, caplog):
        # The ordinary shape for an install that configures only one of the
        # two sections; warning here would be noise on every session start.
        with caplog.at_level(logging.WARNING, logger="notebook_intelligence.mcp_manager"):
            assert _config_section({}, "mcpServers") == {}
        assert caplog.records == []

    @pytest.mark.parametrize(
        "bad", [None, "mcpServers", 42, [], ["my-server"], True]
    )
    def test_present_but_unusable_value_degrades_and_warns(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="notebook_intelligence.mcp_manager"):
            assert _config_section({"mcpServers": bad}, "mcpServers") == {}
        assert len(caplog.records) == 1
        # The message has to name the key: the loader reads two sections of
        # the same shape, and "invalid mcp.json" alone would not say which.
        assert "mcpServers" in caplog.records[0].getMessage()


class TestUpdateMcpServersWithMalformedSections:
    def _manager(self, mcp_config):
        return MCPManager(mcp_config)

    def test_null_mcp_servers_does_not_raise(self):
        # The reported crash: this constructor raised AttributeError.
        manager = self._manager({"mcpServers": None})

        assert manager.get_mcp_servers() == []

    def test_null_participants_does_not_raise(self):
        # Same defect, next line, never reported: participants is read
        # identically and iterated directly.
        manager = self._manager({"participants": None})

        assert manager.get_mcp_participants() == []

    def test_both_sections_null(self):
        manager = self._manager({"mcpServers": None, "participants": None})

        assert manager.get_mcp_servers() == []
        assert manager.get_mcp_participants() == []

    @pytest.mark.parametrize("bad", [None, "", [], 0])
    def test_wrong_typed_sections_degrade_to_no_servers(self, bad):
        manager = self._manager({"mcpServers": bad, "participants": bad})

        assert manager.get_mcp_servers() == []
        assert manager.get_mcp_participants() == []

    def test_empty_config_still_works(self):
        # Guards against a fix that only handled the malformed cases and
        # broke the ordinary empty one.
        manager = self._manager({})

        assert manager.get_mcp_servers() == []
        assert manager.get_mcp_participants() == []

    def test_a_disabled_server_is_still_skipped(self):
        # The valid path has to survive the coercion: a real section must
        # reach create_servers, and `disabled` must still be honored.
        manager = self._manager(
            {"mcpServers": {"off": {"command": "uvx", "disabled": True}}}
        )

        assert manager.get_mcp_servers() == []

    def test_reload_with_a_malformed_section_clears_prior_state(self):
        # update_mcp_servers is also the reload path (called again on every
        # config POST), so a file that goes malformed must leave the manager
        # empty rather than half-populated.
        manager = self._manager({})

        manager.update_mcp_servers({"mcpServers": None})

        assert manager.get_mcp_servers() == []
        assert manager.get_mcp_participants() == []
