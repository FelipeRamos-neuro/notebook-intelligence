// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  CommandIDs,
  executeResponseStreamCommand,
  isResponseStreamCommandAllowed,
  RESPONSE_BUTTON_COMMAND_ALLOWLIST,
  RUN_UI_COMMAND_ALLOWLIST
} from '../../src/command-ids';

// Issue #441: the chat sidebar handed a streamed `commandId` straight to
// `app.commands.execute`, so anything registered in the application was
// reachable from response content. Every backend caller passes a literal
// today, which is why this is hardening rather than a fix for a live
// exposure, and why the tests below are about the policy rather than about
// reproducing an exploit.

describe('isResponseStreamCommandAllowed', () => {
  it('allows an id the backend actually drives', () => {
    expect(
      isResponseStreamCommandAllowed(
        CommandIDs.runCellAtIndex,
        RUN_UI_COMMAND_ALLOWLIST
      )
    ).toBe(true);
  });

  it('refuses an id the backend never sends', () => {
    // A registered JupyterLab command that NBI has no reason to invoke.
    // Before the gate this executed exactly like any other.
    expect(
      isResponseStreamCommandAllowed(
        'terminal:create-new',
        RUN_UI_COMMAND_ALLOWLIST
      )
    ).toBe(false);
  });

  it('refuses an unregistered id', () => {
    expect(
      isResponseStreamCommandAllowed(
        'attacker:do-something',
        RUN_UI_COMMAND_ALLOWLIST
      )
    ).toBe(false);
  });

  it.each([
    ['empty', ''],
    ['undefined', undefined],
    ['null', null],
    ['a number', 42],
    ['an object', {}]
  ])('refuses %s rather than throwing', (_label, value) => {
    // `commandId` arrives off the wire, so it is not guaranteed to be a
    // string however the type declares it.
    expect(
      isResponseStreamCommandAllowed(
        value as unknown as string,
        RUN_UI_COMMAND_ALLOWLIST
      )
    ).toBe(false);
  });
});

describe('executeResponseStreamCommand', () => {
  // These are the tests that pin the enforcement rather than the policy.
  // Testing the predicate and the lists alone left a hole: deleting a
  // sink's check outright kept every other test in this file passing,
  // because nothing asserted that a sink consults the allowlist at all.
  // Asserting on the spy is what closes that.

  it('never reaches execute for an id nothing sends', async () => {
    const execute = jest.fn().mockResolvedValue('ran');

    const result = await executeResponseStreamCommand(
      execute,
      'terminal:create-new',
      { cwd: '/' },
      RUN_UI_COMMAND_ALLOWLIST
    );

    expect(execute).not.toHaveBeenCalled();
    expect(result).toContain('is not an allowed UI command');
  });

  it('runs an allowed id and passes its args through untouched', async () => {
    const execute = jest.fn().mockResolvedValue('cell output');
    const args = { cellIndex: 3, notebookPath: 'work/a.ipynb' };

    const result = await executeResponseStreamCommand(
      execute,
      CommandIDs.runCellAtIndex,
      args,
      RUN_UI_COMMAND_ALLOWLIST
    );

    expect(execute).toHaveBeenCalledWith(CommandIDs.runCellAtIndex, args);
    expect(result).toBe('cell output');
  });

  it('returns the refusal instead of throwing', async () => {
    // The RunUICommand branches send this value back to the backend caller
    // waiting on a callback, so a throw would surface as a hang rather than
    // an answer.
    const execute = jest.fn();

    await expect(
      executeResponseStreamCommand(
        execute,
        'attacker:do-something',
        {},
        RUN_UI_COMMAND_ALLOWLIST
      )
    ).resolves.toContain('is not an allowed UI command');
  });

  it('applies whichever allowlist it is handed', async () => {
    // One wrapper, two policies: an id that is legitimate for a tool call
    // must still be refused when it arrives from a button.
    const execute = jest.fn().mockResolvedValue('ran');

    await executeResponseStreamCommand(
      execute,
      CommandIDs.runCommandInTerminal,
      { command: 'rm -rf /' },
      RESPONSE_BUTTON_COMMAND_ALLOWLIST
    );

    expect(execute).not.toHaveBeenCalled();
  });

  it('lets the settings dialog through on the button path', async () => {
    const execute = jest.fn().mockResolvedValue(undefined);

    await executeResponseStreamCommand(
      execute,
      CommandIDs.openConfigurationDialog,
      undefined,
      RESPONSE_BUTTON_COMMAND_ALLOWLIST
    );

    expect(execute).toHaveBeenCalledTimes(1);
  });
});

describe('the two allowlists', () => {
  it('lets the button sink run only the settings dialog', () => {
    // The sole ButtonData the backend constructs offers "Configure".
    expect(Array.from(RESPONSE_BUTTON_COMMAND_ALLOWLIST)).toEqual([
      CommandIDs.openConfigurationDialog
    ]);
  });

  it('keeps the button list strictly narrower than the tool list', () => {
    // The button path needs one id; the tool path needs twenty-one. Sharing
    // one list would hand a clickable button the whole notebook tool surface.
    expect(RESPONSE_BUTTON_COMMAND_ALLOWLIST.size).toBeLessThan(
      RUN_UI_COMMAND_ALLOWLIST.size
    );
    for (const id of RESPONSE_BUTTON_COMMAND_ALLOWLIST) {
      expect(RUN_UI_COMMAND_ALLOWLIST.has(id)).toBe(true);
    }
  });

  it('refuses a tool-path id on the button path', () => {
    // Pins the separation: `run-command-in-terminal` is legitimately
    // reachable from a tool call and must not be reachable from a button.
    expect(
      isResponseStreamCommandAllowed(
        CommandIDs.runCommandInTerminal,
        RESPONSE_BUTTON_COMMAND_ALLOWLIST
      )
    ).toBe(false);
  });

  it('is not simply every CommandIDs value', () => {
    // 17 of the 38 registered ids are frontend-only (launcher tiles, editor
    // context actions, the tour) and never arrive over the wire. Widening
    // the list to the whole namespace would undo the point of having one.
    const allIds = Object.values(CommandIDs).filter(
      value => typeof value === 'string'
    );
    expect(allIds.length).toBeGreaterThan(RUN_UI_COMMAND_ALLOWLIST.size);
  });

  it('includes the JupyterLab commands the notebook tools drive', () => {
    // These are not NBI ids, so a list built from `CommandIDs` alone would
    // have silently broken saving and opening documents.
    expect(RUN_UI_COMMAND_ALLOWLIST.has('docmanager:save')).toBe(true);
    expect(RUN_UI_COMMAND_ALLOWLIST.has('docmanager:open')).toBe(true);
  });
});
