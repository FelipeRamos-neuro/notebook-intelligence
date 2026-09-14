// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Centralized command-id constants for the JupyterLab plugin. Hoisted out
// of `src/index.ts` so non-JL modules (and Jest tests) can import them
// without pulling the JupyterLab packages into their dependency graph.

export namespace CommandIDs {
  export const chatuserInput = 'notebook-intelligence:chat-user-input';
  export const insertAtCursor = 'notebook-intelligence:insert-at-cursor';
  export const addCodeAsNewCell = 'notebook-intelligence:add-code-as-new-cell';
  export const createNewFile = 'notebook-intelligence:create-new-file';
  export const createNewNotebook = 'notebook-intelligence:create-new-notebook';
  export const listAvailableNotebookKernels =
    'notebook-intelligence:list-available-notebook-kernels';
  export const renameNotebook = 'notebook-intelligence:rename-notebook';
  export const addCodeCellToNotebook =
    'notebook-intelligence:add-code-cell-to-notebook';
  export const addMarkdownCellToNotebook =
    'notebook-intelligence:add-markdown-cell-to-notebook';
  export const editorGenerateCode =
    'notebook-intelligence:editor-generate-code';
  export const editorExplainThisCode =
    'notebook-intelligence:editor-explain-this-code';
  export const editorFixThisCode = 'notebook-intelligence:editor-fix-this-code';
  export const editorExplainThisOutput =
    'notebook-intelligence:editor-explain-this-output';
  export const editorTroubleshootThisOutput =
    'notebook-intelligence:editor-troubleshoot-this-output';
  export const editorAskAboutThisOutput =
    'notebook-intelligence:editor-ask-about-this-output';
  export const openGitHubCopilotLoginDialog =
    'notebook-intelligence:open-github-copilot-login-dialog';
  export const openConfigurationDialog =
    'notebook-intelligence:open-configuration-dialog';
  export const addMarkdownCellToActiveNotebook =
    'notebook-intelligence:add-markdown-cell-to-active-notebook';
  export const addCodeCellToActiveNotebook =
    'notebook-intelligence:add-code-cell-to-active-notebook';
  export const deleteCellAtIndex = 'notebook-intelligence:delete-cell-at-index';
  export const insertCellAtIndex = 'notebook-intelligence:insert-cell-at-index';
  export const getCellTypeAndSource =
    'notebook-intelligence:get-cell-type-and-source';
  export const setCellTypeAndSource =
    'notebook-intelligence:set-cell-type-and-source';
  export const getNumberOfCells = 'notebook-intelligence:get-number-of-cells';
  export const getCellOutput = 'notebook-intelligence:get-cell-output';
  export const runCellAtIndex = 'notebook-intelligence:run-cell-at-index';
  export const getCurrentFileContent =
    'notebook-intelligence:get-current-file-content';
  export const setCurrentFileContent =
    'notebook-intelligence:set-current-file-content';
  export const openMCPConfigEditor =
    'notebook-intelligence:open-mcp-config-editor';
  export const showFormInputDialog =
    'notebook-intelligence:show-form-input-dialog';
  export const runCommandInTerminal =
    'notebook-intelligence:run-command-in-terminal';
  export const openClaudeCodeLauncher =
    'notebook-intelligence:open-claude-code-launcher';
  export const openOpenCodeLauncher =
    'notebook-intelligence:open-opencode-launcher';
  export const openPiLauncher = 'notebook-intelligence:open-pi-launcher';
  export const openGitHubCopilotCliLauncher =
    'notebook-intelligence:open-github-copilot-cli-launcher';
  export const openCodexLauncher = 'notebook-intelligence:open-codex-launcher';
  export const showTour = 'notebook-intelligence:show-tour';
  export const focusChatInput = 'notebook-intelligence:focus-chat-input';
}

// Command ids the response stream is allowed to execute.
//
// The chat sidebar hands a streamed `commandId` straight to
// `app.commands.execute`, so without a check anything registered in the
// application is reachable from response content (#441). Every server-side
// caller passes a hardcoded literal today, so this is structural hardening
// rather than a fix for a live exposure: the day a tool derives an id from
// model output, or a third-party extension streams its own `ButtonData`
// (`commandId` is a free-form string on a public dataclass), the check is
// what stands between that and an arbitrary command.
//
// Scope limit worth knowing: this gates the id, not the arguments.
// `runCommandInTerminal` is a legitimate entry whose handler writes its
// `command` argument into a shell unattended, so an allowlist does not make
// the response stream safe on its own.
//
// Two lists because the legitimate sets differ by an order of magnitude.
// Keep them beside `CommandIDs` so a new server-driven command is obvious.

/**
 * Ids the backend drives through `RunUICommand`, which executes with no user
 * interaction. Includes two JupyterLab built-ins the tools legitimately use,
 * so this is deliberately not "every value in `CommandIDs`" (17 of those are
 * frontend-only and never arrive over the wire).
 */
export const RUN_UI_COMMAND_ALLOWLIST: ReadonlySet<string> = new Set([
  CommandIDs.createNewFile,
  CommandIDs.createNewNotebook,
  CommandIDs.listAvailableNotebookKernels,
  CommandIDs.renameNotebook,
  CommandIDs.addCodeCellToNotebook,
  CommandIDs.addMarkdownCellToNotebook,
  CommandIDs.addCodeCellToActiveNotebook,
  CommandIDs.addMarkdownCellToActiveNotebook,
  CommandIDs.deleteCellAtIndex,
  CommandIDs.insertCellAtIndex,
  CommandIDs.getCellTypeAndSource,
  CommandIDs.setCellTypeAndSource,
  CommandIDs.getNumberOfCells,
  CommandIDs.getCellOutput,
  CommandIDs.runCellAtIndex,
  CommandIDs.getCurrentFileContent,
  CommandIDs.setCurrentFileContent,
  CommandIDs.openConfigurationDialog,
  CommandIDs.runCommandInTerminal,
  // JupyterLab's own commands, driven by the notebook tools.
  'docmanager:open',
  'docmanager:save'
]);

/**
 * Ids a streamed button may execute on click. Narrower than the tool path:
 * the only `ButtonData` constructed in the tree offers the settings dialog.
 */
export const RESPONSE_BUTTON_COMMAND_ALLOWLIST: ReadonlySet<string> = new Set([
  CommandIDs.openConfigurationDialog
]);

/**
 * Whether `commandId` may be executed from the response stream.
 *
 * Unknown ids are refused rather than passed through, which is the opposite
 * default from the rest of this surface and intentional: a command id that
 * nothing in the backend sends is either a mistake or someone else's idea.
 */
export function isResponseStreamCommandAllowed(
  commandId: string,
  allowlist: ReadonlySet<string>
): boolean {
  return typeof commandId === 'string' && allowlist.has(commandId);
}

/**
 * Run a command that came off the response stream, or refuse it.
 *
 * The sinks call through here rather than checking the allowlist themselves.
 * Testing the predicate and the lists in isolation left the enforcement
 * unpinned: deleting a sink's check outright kept every test passing,
 * because nothing asserted the sinks consult the policy at all. Routing the
 * three of them through one function makes that testable without standing up
 * a JupyterFrontEnd, which is why this lives here (the module imports no
 * JupyterLab packages and is already Jest-reachable) rather than in
 * chat-sidebar.
 *
 * Returns the command's own result when it runs. A refusal comes back as an
 * error string instead of throwing, because both callers report it onward:
 * the RunUICommand branches send it to the waiting backend caller, so a
 * refusal has to be an answer rather than a hang.
 */
export async function executeResponseStreamCommand(
  execute: (commandId: string, args: unknown) => Promise<unknown>,
  commandId: string,
  args: unknown,
  allowlist: ReadonlySet<string>
): Promise<unknown> {
  if (!isResponseStreamCommandAllowed(commandId, allowlist)) {
    const refusal = `Error executing command: '${commandId}' is not an allowed UI command`;
    console.warn(`[NBI] ${refusal}`);
    return refusal;
  }
  return execute(commandId, args);
}
