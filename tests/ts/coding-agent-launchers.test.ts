// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { NBIConfig } from '../../src/api';

describe('NBIConfig.isCodingAgentLauncherDisabledByPolicy', () => {
  it('fails closed (returns true) when capabilities have not loaded', () => {
    // Defense in depth: if the capabilities field is missing entirely, an
    // admin denylist must not silently disappear. The companion
    // is*CliAvailable getters also default to false so the tile is hidden
    // anyway on first paint, but this gate is the load-bearing one once
    // capabilities arrive.
    const config = new NBIConfig();
    expect(config.isCodingAgentLauncherDisabledByPolicy('claude-code')).toBe(
      true
    );
  });

  it('returns false when the disabled list is empty', () => {
    const config = new NBIConfig();
    config.capabilities = { disabled_coding_agent_launchers: [] };
    expect(config.isCodingAgentLauncherDisabledByPolicy('claude-code')).toBe(
      false
    );
  });

  it('returns true for an exact match in the list', () => {
    const config = new NBIConfig();
    config.capabilities = {
      disabled_coding_agent_launchers: ['claude-code', 'opencode']
    };
    expect(config.isCodingAgentLauncherDisabledByPolicy('claude-code')).toBe(
      true
    );
    expect(config.isCodingAgentLauncherDisabledByPolicy('opencode')).toBe(true);
  });

  it('returns false for IDs not in the list', () => {
    const config = new NBIConfig();
    config.capabilities = {
      disabled_coding_agent_launchers: ['claude-code']
    };
    expect(config.isCodingAgentLauncherDisabledByPolicy('opencode')).toBe(
      false
    );
    expect(
      config.isCodingAgentLauncherDisabledByPolicy('github-copilot-cli')
    ).toBe(false);
    expect(config.isCodingAgentLauncherDisabledByPolicy('codex')).toBe(false);
  });

  it('does not match the LLM-provider ID for the Copilot tile', () => {
    // A test against accidentally treating `github-copilot` (the provider
    // ID used by `disabled_providers`) as the launcher-tile ID. The two
    // surfaces are deliberately distinct: the tile ID is
    // `github-copilot-cli`.
    const config = new NBIConfig();
    config.capabilities = {
      disabled_coding_agent_launchers: ['github-copilot']
    };
    expect(
      config.isCodingAgentLauncherDisabledByPolicy('github-copilot-cli')
    ).toBe(false);
  });

  it('fails closed on a malformed backend payload', () => {
    // A non-array value means the backend is broken. The gate fails safe:
    // hide the tile rather than let a malformed payload silently bypass
    // the admin denylist.
    for (const value of ['claude-code', null, 42, {}]) {
      const config = new NBIConfig();
      config.capabilities = { disabled_coding_agent_launchers: value };
      expect(config.isCodingAgentLauncherDisabledByPolicy('claude-code')).toBe(
        true
      );
    }
  });
});
