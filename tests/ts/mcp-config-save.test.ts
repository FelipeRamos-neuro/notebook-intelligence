// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Saving malformed JSON in the MCP config editor used to throw out of the save
// handler before its try block, so the edit was dropped with no message
// anywhere but the devtools console.
import {
  describeMCPConfigSaveError,
  saveMCPConfig
} from '../../src/mcp-config-save';

function fakeApi(setResult?: { reject: unknown }) {
  return {
    setMCPConfigFile: jest.fn(async () => {
      if (setResult) {
        throw setResult.reject;
      }
      return {};
    }),
    fetchCapabilities: jest.fn(async () => undefined)
  };
}

describe('saveMCPConfig', () => {
  it('reports a parse failure without reaching the server', async () => {
    const api = fakeApi();

    const error = await saveMCPConfig(api, () =>
      JSON.parse('{"mcpServers": {')
    );

    expect(error).toContain('not valid JSON');
    expect(api.setMCPConfigFile).not.toHaveBeenCalled();
    expect(api.fetchCapabilities).not.toHaveBeenCalled();
  });

  it('does not refresh capabilities when the server rejects the save', async () => {
    const api = fakeApi({ reject: new Error('mcpServers must be an object') });

    const error = await saveMCPConfig(api, () => ({ mcpServers: [] }));

    expect(error).toBe('mcpServers must be an object');
    expect(api.setMCPConfigFile).toHaveBeenCalledTimes(1);
    expect(api.fetchCapabilities).not.toHaveBeenCalled();
  });

  it('posts the parsed config and refreshes capabilities on success', async () => {
    const api = fakeApi();
    const config = { mcpServers: { local: { command: 'node' } } };

    const error = await saveMCPConfig(api, () => config);

    expect(error).toBeNull();
    expect(api.setMCPConfigFile).toHaveBeenCalledWith(config);
    expect(api.fetchCapabilities).toHaveBeenCalledTimes(1);
  });

  it('keeps a successful save successful when the capabilities refresh fails', async () => {
    const api = fakeApi();
    api.fetchCapabilities.mockRejectedValueOnce(new Error('server restarting'));

    await expect(saveMCPConfig(api, () => ({}))).resolves.toBeNull();
  });
});

describe('describeMCPConfigSaveError', () => {
  it('separates a syntax error from a server error carrying the same text', () => {
    let parseFailure: unknown;
    try {
      JSON.parse('{"mcpServers": {');
    } catch (reason) {
      parseFailure = reason;
    }
    const message = (parseFailure as SyntaxError).message;

    expect(describeMCPConfigSaveError(parseFailure)).toBe(
      `not valid JSON. ${message}`
    );
    expect(describeMCPConfigSaveError(new Error(message))).toBe(message);
  });

  it('keeps the parser position, which is what locates the typo', () => {
    let parseFailure: unknown;
    try {
      JSON.parse('{\n  "mcpServers": {\n    "a": "b"\n');
    } catch (reason) {
      parseFailure = reason;
    }

    expect(describeMCPConfigSaveError(parseFailure)).toMatch(/position \d+/);
  });

  it('never leaves the notification empty after the colon', () => {
    // `reason?.message ?? reason` left an empty tail for these, since `??`
    // does not fall back on an empty string.
    expect(describeMCPConfigSaveError({ message: '' })).not.toBe('');
    expect(describeMCPConfigSaveError(undefined)).toBe('undefined');
  });

  it('does not print [object Object] at the user', () => {
    const described = describeMCPConfigSaveError({ code: 400 });

    expect(described).not.toContain('[object Object]');
    expect(described).toBe('the save failed for an unknown reason');
  });

  it('prefers a non-empty message over the value itself', () => {
    const reason = { message: 'boom', toString: () => 'ignored' };

    expect(describeMCPConfigSaveError(reason)).toBe('boom');
  });

  it('ignores a non-string message instead of interpolating it', () => {
    const reason = { message: { code: 400 }, toString: () => 'ServerError' };

    expect(describeMCPConfigSaveError(reason)).toBe('ServerError');
  });
});
