// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Saving the MCP config editor reads the open document and then posts it, so a
 * save can fail on either half. Reading the document is a `JSON.parse` of the
 * editor's text, which means a typo in the user's edit throws rather than
 * reaching the server. `readConfig` is a thunk so that read cannot end up
 * outside the guarded path: both failures return a description here instead of
 * throwing out of a signal handler with nowhere to put them.
 */
export async function saveMCPConfig(
  api: {
    setMCPConfigFile(config: unknown): Promise<unknown>;
    fetchCapabilities(): Promise<void>;
  },
  readConfig: () => unknown
): Promise<string | null> {
  try {
    const config = readConfig();
    await api.setMCPConfigFile(config);
  } catch (reason) {
    return describeMCPConfigSaveError(reason);
  }
  // The config did save, so a capabilities refresh that fails is not a save
  // failure and must not be reported as one. `fetchCapabilities` logs its own
  // reason.
  await api.fetchCapabilities().catch(() => undefined);
  return null;
}

/**
 * The two failures need different words: a syntax error is the user's to fix in
 * the editor in front of them, while a server rejection is about the config's
 * shape.
 *
 * `ServerConnection.ResponseError` carries the handler's JSON `message` field
 * on `reason.message`, which is why a rejection is reported by its message
 * rather than by the error itself.
 */
export function describeMCPConfigSaveError(reason: unknown): string {
  if (reason instanceof SyntaxError) {
    // Kept short on purpose: JupyterLab truncates a notification at 140
    // characters, and the parser's trailing "(line 64 column 10)" is the part
    // worth reading. With the caller's prefix a real config lands within a few
    // characters of the limit.
    return `not valid JSON. ${reason.message}`;
  }
  const message = (reason as { message?: unknown })?.message;
  // An empty message is treated as absent so the notification never ends at
  // the caller's colon with nothing after it.
  if (typeof message === 'string' && message !== '') {
    return message;
  }
  const text = String(reason);
  // `String()` on a plain object is `[object Object]`, which tells the user
  // nothing and reads like a bug in the notification itself.
  return text === '[object Object]'
    ? 'the save failed for an unknown reason'
    : text;
}
