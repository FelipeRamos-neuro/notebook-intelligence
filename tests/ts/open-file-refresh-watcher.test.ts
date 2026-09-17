// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  attachOpenFileRefreshWatcher,
  formatRevertNotification,
  IRefreshWatcherEnv,
  shouldNotifyRevert,
  shouldRevertContext,
  WATCHED_SHELL_AREAS
} from '../../src/open-file-refresh-watcher';

describe('WATCHED_SHELL_AREAS', () => {
  it('excludes "down" because LabShell.widgets() throws on it at runtime', () => {
    // Regression pin for PR #330 review feedback (mbektas). 'down' is
    // present in JupyterLab's TypeScript Area union but absent from
    // LabShell.widgets()'s runtime switch; including it would throw
    // `Invalid area: down` and surface as an unhandled promise
    // rejection on every poll tick.
    expect(WATCHED_SHELL_AREAS).not.toContain('down');
  });

  it('includes "main" so the primary editor area is always covered', () => {
    expect(WATCHED_SHELL_AREAS).toContain('main');
  });

  it('contains only areas LabShell.widgets() implements', () => {
    // The runtime switch at @jupyterlab/application/lib/shell.js
    // handles: main, left, right, header, top, menu, bottom. We walk
    // the document-hosting subset (main, left, right); chrome areas
    // (header/top/menu/bottom) never host DocumentWidget. Pin the
    // exact set so a future widening that re-adds 'down' (or any
    // unimplemented area) trips this assertion.
    const RUNTIME_IMPLEMENTED = new Set([
      'main',
      'left',
      'right',
      'header',
      'top',
      'menu',
      'bottom'
    ]);
    for (const area of WATCHED_SHELL_AREAS) {
      expect(RUNTIME_IMPLEMENTED).toContain(area);
    }
  });
});

describe('shouldRevertContext', () => {
  const base = {
    isDirty: false,
    isReady: true,
    isDisposed: false,
    isKernelBusy: false,
    contextLastModified: '2026-01-01T00:00:00.000000Z',
    diskLastModified: '2026-01-01T00:00:00.000000Z'
  };

  it('skips while the kernel is busy so a running cell is not swapped out', () => {
    // Starting an execution marks the model dirty, so the dirty guard
    // covers a run on its own. The gap is autosave: JupyterLab saves
    // every 120s by default, and a save landing mid-execution clears
    // dirty while the kernel keeps working, so a cell running longer
    // than that is clean and busy for the rest of its life. That is
    // where a revert destroys the in-flight result (#429).
    expect(
      shouldRevertContext({
        ...base,
        isKernelBusy: true,
        diskLastModified: '2026-01-02T00:00:00.000000Z'
      })
    ).toBe(false);
  });

  // A companion "reverts once the kernel is idle again" case was removed
  // rather than kept as a positive control: `base` already carries
  // `isKernelBusy: false`, so it restated the pre-existing "reverts when
  // disk is strictly newer than the context" test and killed no mutant
  // that test did not already kill.

  it('reverts when disk is strictly newer than the context', () => {
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: '2026-01-01T00:00:01.000000Z'
      })
    ).toBe(true);
  });

  it('skips when timestamps match (already current)', () => {
    expect(shouldRevertContext(base)).toBe(false);
  });

  it('skips when disk is older (something rolled back the file)', () => {
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: '2025-12-31T23:59:59.000000Z'
      })
    ).toBe(false);
  });

  it('skips when the model is dirty so user edits are never clobbered', () => {
    // The standard Lab "newer on disk" prompt fires at save time and is
    // the right place to involve the user; the silent revert path
    // should defer to it.
    expect(
      shouldRevertContext({
        ...base,
        isDirty: true,
        diskLastModified: '2026-01-02T00:00:00.000000Z'
      })
    ).toBe(false);
  });

  it('skips when the context is not yet ready', () => {
    // Reverting before populate() finishes would race the initial
    // load and produce a spurious revert that re-fetches the same
    // bytes the context is already pulling.
    expect(
      shouldRevertContext({
        ...base,
        isReady: false,
        diskLastModified: '2026-01-02T00:00:00.000000Z'
      })
    ).toBe(false);
  });

  it('skips when the context is disposed', () => {
    expect(
      shouldRevertContext({
        ...base,
        isDisposed: true,
        diskLastModified: '2026-01-02T00:00:00.000000Z'
      })
    ).toBe(false);
  });

  it('skips when either timestamp is missing', () => {
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: undefined,
        contextLastModified: '2026-01-01T00:00:00.000000Z'
      })
    ).toBe(false);
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: '2026-01-01T00:00:00.000000Z',
        contextLastModified: null
      })
    ).toBe(false);
  });

  it('reverts on a sub-second forward movement', () => {
    // Numeric compare is what makes fractional-second detection
    // possible; a hypothetical "round to seconds then compare"
    // regression would still pass the existing 1s-delta test but
    // would fail this one.
    expect(
      shouldRevertContext({
        ...base,
        contextLastModified: '2026-01-01T00:00:00.000000Z',
        diskLastModified: '2026-01-01T00:00:00.500000Z'
      })
    ).toBe(true);
  });

  it('treats "...:56Z" and "...:56.000000Z" as the same instant', () => {
    // jupyter_server's datetime.isoformat() omits the fractional
    // component when microsecond == 0, so the same mtime can arrive
    // as either form across calls. Lexicographic compare puts
    // "...:56.000000Z" below "...:56Z" (the `.` at 0x2E sorts below
    // the `Z` at 0x5A), so a string compare would fire a spurious
    // revert when the context happens to hold the fractional form
    // and disk reports the bare-second form for the same instant.
    // Numeric compare via Date.parse collapses both to the same
    // epoch ms.
    expect(
      shouldRevertContext({
        ...base,
        contextLastModified: '2026-01-01T00:00:56.000000Z',
        diskLastModified: '2026-01-01T00:00:56Z'
      })
    ).toBe(false);
    // Symmetric: bare-second context and fractional disk for the
    // same instant should also not revert.
    expect(
      shouldRevertContext({
        ...base,
        contextLastModified: '2026-01-01T00:00:56Z',
        diskLastModified: '2026-01-01T00:00:56.000000Z'
      })
    ).toBe(false);
  });

  it('skips when either timestamp is unparseable', () => {
    // new Date(unparseable).getTime() returns NaN. NaN > anything is
    // false, so a malformed timestamp degrades to "don't revert."
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: 'not-a-date',
        contextLastModified: '2026-01-01T00:00:00.000000Z'
      })
    ).toBe(false);
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: '2026-01-01T00:00:00.000000Z',
        contextLastModified: 'not-a-date'
      })
    ).toBe(false);
    // Both sides unparseable: NaN > NaN is also false.
    expect(
      shouldRevertContext({
        ...base,
        diskLastModified: 'not-a-date',
        contextLastModified: 'also-not-a-date'
      })
    ).toBe(false);
  });
});

describe('shouldNotifyRevert', () => {
  it('notifies for the document the user is looking at', () => {
    expect(
      shouldNotifyRevert('work/notebook.ipynb', 'work/notebook.ipynb')
    ).toBe(true);
  });

  it('stays silent for a background document', () => {
    // The reason to notify is that the cursor and scroll position moved
    // under the user, which only happens on screen. An unscoped toast
    // turned one agent run over six files into six assertive
    // announcements stacked on top of each other.
    expect(shouldNotifyRevert('work/other.ipynb', 'work/notebook.ipynb')).toBe(
      false
    );
  });

  it.each([
    ['empty', ''],
    ['null', null],
    ['undefined', undefined]
  ])('stays silent when the active path is %s', (_label, activePath) => {
    // ActiveDocumentWatcher reports '' with nothing open. Matching on a
    // falsy value would notify for every revert in the session.
    expect(shouldNotifyRevert('work/notebook.ipynb', activePath)).toBe(false);
  });

  it('stays silent when the reverted path is empty', () => {
    expect(shouldNotifyRevert('', '')).toBe(false);
  });
});

describe('formatRevertNotification', () => {
  it('names the full path, not the basename', () => {
    // JupyterLab's own "File Changed" dialog interpolates the full path
    // into the analogous message, and two open files sharing a basename
    // would otherwise produce identical text.
    expect(formatRevertNotification('work/utils.py')).toContain(
      'work/utils.py'
    );
  });

  it('distinguishes two files that share a basename', () => {
    expect(formatRevertNotification('a/utils.py')).not.toEqual(
      formatRevertNotification('b/utils.py')
    );
  });

  it('says what happened and why, without naming a culprit', () => {
    const message = formatRevertNotification('work/utils.py');

    expect(message).toContain('changed on disk');
    expect(message).toContain('reloaded');
    // The watcher only ever sees a newer mtime; a terminal command, a
    // sync client or a git checkout produces that just as readily as an
    // agent, so the message must not blame one.
    expect(message.toLowerCase()).not.toContain('agent');
  });
});

interface IFakeKernel {
  status: string;
}

interface IFakeSessionContext {
  session: { kernel: IFakeKernel | null } | null;
}

interface IFakeContext {
  path: string;
  contentsModel: { last_modified: string } | null;
  model: { dirty: boolean };
  isReady: boolean;
  isDisposed: boolean;
  // Mirrors the optional chain the watcher walks. `null` at any level
  // is the ordinary case for a document with no kernel.
  sessionContext: IFakeSessionContext | null;
  revert: jest.Mock<Promise<void>, []>;
}

interface IFakeWidget {
  context: IFakeContext;
}

function makeContext(overrides: Partial<IFakeContext> = {}): IFakeContext {
  return {
    path: 'notebook.ipynb',
    contentsModel: { last_modified: '2026-01-01T00:00:00.000000Z' },
    model: { dirty: false },
    isReady: true,
    isDisposed: false,
    sessionContext: { session: { kernel: { status: 'idle' } } },
    revert: jest.fn().mockResolvedValue(undefined),
    ...overrides
  };
}

function makeEnv(
  widgets: IFakeWidget[],
  diskByPath: Record<string, string | Error>
): {
  env: IRefreshWatcherEnv;
  fireTick: () => Promise<void>;
} {
  let tickHandler: (() => void) | null = null;
  return {
    env: {
      iterDocumentWidgets: () => widgets,
      fetchDiskModel: async path => {
        const entry = diskByPath[path];
        if (entry instanceof Error) {
          throw entry;
        }
        if (entry === undefined) {
          throw new Error(`no fake disk entry for ${path}`);
        }
        return {
          name: path.split('/').pop() ?? path,
          path,
          type: 'file',
          writable: true,
          created: '2026-01-01T00:00:00.000000Z',
          last_modified: entry,
          mimetype: 'text/plain',
          content: null,
          format: null
        };
      },
      setInterval: handler => {
        tickHandler = handler;
        return 'fake-handle';
      },
      clearInterval: () => {
        tickHandler = null;
      }
    },
    fireTick: async () => {
      if (!tickHandler) {
        throw new Error('setInterval was never called');
      }
      tickHandler();
      // Tick runs an async loop; flush microtasks so jest assertions
      // see the post-tick state.
      await new Promise(resolve => setTimeout(resolve, 0));
      await new Promise(resolve => setTimeout(resolve, 0));
    }
  };
}

describe('attachOpenFileRefreshWatcher', () => {
  it('reverts an open widget when its file is newer on disk', async () => {
    const ctx = makeContext();
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    const onRevert = jest.fn();
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true, onRevert });

    await fireTick();

    expect(ctx.revert).toHaveBeenCalledTimes(1);
    expect(onRevert).toHaveBeenCalledWith('notebook.ipynb');
  });

  it('does not call revert when the toggle is disabled', async () => {
    const ctx = makeContext();
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    let enabled = false;
    attachOpenFileRefreshWatcher({ env, isEnabled: () => enabled });

    await fireTick();
    expect(ctx.revert).not.toHaveBeenCalled();

    enabled = true;
    await fireTick();
    expect(ctx.revert).toHaveBeenCalledTimes(1);
  });

  it('dedupes by path so split-view widgets do not double-revert', async () => {
    // A notebook plus its console-view share one context; the revert
    // should fire once per shared context, not once per widget.
    const ctx = makeContext();
    const { env, fireTick } = makeEnv([{ context: ctx }, { context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    await fireTick();
    expect(ctx.revert).toHaveBeenCalledTimes(1);
  });

  it('skips dirty contexts so user edits survive a tick', async () => {
    const ctx = makeContext({ model: { dirty: true } });
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    await fireTick();
    expect(ctx.revert).not.toHaveBeenCalled();
  });

  it('skips a context whose kernel is busy', async () => {
    const ctx = makeContext({
      sessionContext: { session: { kernel: { status: 'busy' } } }
    });
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    await fireTick();
    expect(ctx.revert).not.toHaveBeenCalled();
  });

  // it.each rather than a loop inside one `it`: a failing iteration used to
  // abort the test, so the later shapes contributed no coverage and the
  // failure never said which shape broke.
  it.each([
    ['no sessionContext', null],
    ['no session', { session: null }],
    ['session without a kernel', { session: { kernel: null } }]
  ])('still reverts a document that has %s', async (_label, sessionContext) => {
    // The guard must not cost plain text files their refresh: every
    // non-notebook document the watcher walks has no session, and the
    // optional chain has to read that as "not busy" rather than as
    // unknown-so-skip.
    const ctx = makeContext({
      path: 'notes.md',
      sessionContext: sessionContext as IFakeSessionContext | null
    });
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notes.md': '2026-01-01T00:00:05.000000Z'
    });
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    await fireTick();
    expect(ctx.revert).toHaveBeenCalledTimes(1);
  });

  it('skips the revert when the kernel goes busy after the decision', async () => {
    // Pins the post-decision re-check specifically. The sibling test below
    // cannot: both kernel reads happen after the disk fetch resolves with
    // no await between them, so flipping the state before release() is
    // already visible to the FIRST read, and deleting the re-check leaves
    // every other test in this file passing.
    //
    // A getter that reports idle once and busy afterwards puts the two
    // reads on opposite sides of the decision, so the re-check is the only
    // thing that can prevent this revert.
    let reads = 0;
    const ctx = makeContext();
    Object.defineProperty(ctx, 'sessionContext', {
      get: () => {
        reads += 1;
        return {
          session: { kernel: { status: reads === 1 ? 'idle' : 'busy' } }
        };
      }
    });
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    await fireTick();

    expect(reads).toBeGreaterThanOrEqual(2);
    expect(ctx.revert).not.toHaveBeenCalled();
  });

  it('skips revert when the kernel goes busy during the in-flight disk fetch', async () => {
    // Kept for the scenario, not for a specific guard. Execution can start
    // while the Contents.get is still outstanding, and the revert must back
    // off rather than land on a now-running notebook.
    //
    // Either kernel read satisfies this, exactly as the dirty-flip sibling
    // below discloses about itself: the flip happens before release(), so
    // the first read already sees it. Mutation testing proved that, and the
    // post-decision re-check is pinned by the getter-based test above
    // instead.
    const ctx = makeContext();
    let release: (() => void) | null = null;
    const fetchDiskModel = jest.fn().mockImplementation(
      () =>
        new Promise<{
          name: string;
          path: string;
          type: string;
          writable: boolean;
          created: string;
          last_modified: string;
          mimetype: string;
          content: null;
          format: null;
        }>(resolve => {
          release = () =>
            resolve({
              name: 'notebook.ipynb',
              path: 'notebook.ipynb',
              type: 'file',
              writable: true,
              created: '2026-01-01T00:00:00.000000Z',
              last_modified: '2026-01-01T00:00:05.000000Z',
              mimetype: 'text/plain',
              content: null,
              format: null
            });
        })
    );
    let tickHandler: (() => void) | null = null;
    const env: IRefreshWatcherEnv = {
      iterDocumentWidgets: () => [{ context: ctx }],
      fetchDiskModel,
      setInterval: handler => {
        tickHandler = handler;
        return 'h';
      },
      clearInterval: () => {
        tickHandler = null;
      }
    };
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    tickHandler!();
    // The user runs a cell while the disk fetch is outstanding.
    ctx.sessionContext = { session: { kernel: { status: 'busy' } } };
    release!();
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(ctx.revert).not.toHaveBeenCalled();
  });

  it('reports per-path errors instead of throwing out of the tick', async () => {
    const ctx = makeContext();
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': new Error('404 file deleted')
    });
    const onError = jest.fn();
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true, onError });

    await fireTick();

    expect(onError).toHaveBeenCalledWith('notebook.ipynb', expect.any(Error));
    expect(ctx.revert).not.toHaveBeenCalled();
  });

  it('skips a tick that fires while the previous one is still in flight', async () => {
    // Pin the re-entrancy guard: a slow Contents.get must not let the
    // next interval-fired tick pile up checks against the same set of
    // widgets. Without the guard, two overlapping ticks would each
    // call revert() on the same already-newer file.
    const ctx = makeContext();
    let release: (() => void) | null = null;
    const fetchDiskModel = jest.fn().mockImplementation(
      () =>
        new Promise<{
          name: string;
          path: string;
          type: string;
          writable: boolean;
          created: string;
          last_modified: string;
          mimetype: string;
          content: null;
          format: null;
        }>(resolve => {
          release = () =>
            resolve({
              name: 'notebook.ipynb',
              path: 'notebook.ipynb',
              type: 'file',
              writable: true,
              created: '2026-01-01T00:00:00.000000Z',
              last_modified: '2026-01-01T00:00:05.000000Z',
              mimetype: 'text/plain',
              content: null,
              format: null
            });
        })
    );
    let tickHandler: (() => void) | null = null;
    const env: IRefreshWatcherEnv = {
      iterDocumentWidgets: () => [{ context: ctx }],
      fetchDiskModel,
      setInterval: handler => {
        tickHandler = handler;
        return 'h';
      },
      clearInterval: () => {
        tickHandler = null;
      }
    };
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    tickHandler!();
    // Second tick fires while the first is still awaiting the fetch.
    tickHandler!();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(fetchDiskModel).toHaveBeenCalledTimes(1);

    release!();
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(ctx.revert).toHaveBeenCalledTimes(1);
  });

  it('reports revert() rejections via onError', async () => {
    // Disk-newer decision passes, revert() then throws (e.g. file
    // deleted between the disk-check and the revert call). The error
    // must land in onError, not bubble out and kill the poller.
    const ctx = makeContext();
    ctx.revert.mockRejectedValueOnce(new Error('file vanished'));
    const { env, fireTick } = makeEnv([{ context: ctx }], {
      'notebook.ipynb': '2026-01-01T00:00:05.000000Z'
    });
    const onError = jest.fn();
    const onRevert = jest.fn();
    attachOpenFileRefreshWatcher({
      env,
      isEnabled: () => true,
      onError,
      onRevert
    });

    await fireTick();

    expect(ctx.revert).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith('notebook.ipynb', expect.any(Error));
    // onRevert must not have fired since the revert call itself failed.
    expect(onRevert).not.toHaveBeenCalled();
  });

  it('skips revert when dirty flips during the in-flight disk fetch', async () => {
    // Property test: when the model goes dirty between the start of
    // the disk fetch and the decision point, the watcher backs off.
    // (The initial dirty check inside shouldRevertContext is the
    // load-bearing guard for this scenario; the post-decision
    // re-check is documented as defense-in-depth in the production
    // file. Either guard alone would satisfy this test.)
    const ctx = makeContext();
    let release: (() => void) | null = null;
    const fetchDiskModel = jest.fn().mockImplementation(
      () =>
        new Promise<{
          name: string;
          path: string;
          type: string;
          writable: boolean;
          created: string;
          last_modified: string;
          mimetype: string;
          content: null;
          format: null;
        }>(resolve => {
          release = () =>
            resolve({
              name: 'notebook.ipynb',
              path: 'notebook.ipynb',
              type: 'file',
              writable: true,
              created: '2026-01-01T00:00:00.000000Z',
              last_modified: '2026-01-01T00:00:05.000000Z',
              mimetype: 'text/plain',
              content: null,
              format: null
            });
        })
    );
    let tickHandler: (() => void) | null = null;
    const env: IRefreshWatcherEnv = {
      iterDocumentWidgets: () => [{ context: ctx }],
      fetchDiskModel,
      setInterval: handler => {
        tickHandler = handler;
        return 'h';
      },
      clearInterval: () => {
        tickHandler = null;
      }
    };
    attachOpenFileRefreshWatcher({ env, isEnabled: () => true });

    tickHandler!();
    // Simulate a keystroke arriving while the disk fetch is in flight.
    ctx.model.dirty = true;
    release!();
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(ctx.revert).not.toHaveBeenCalled();
  });

  it('clears the interval and no longer runs ticks after teardown', async () => {
    let cleared = false;
    let tickHandler: (() => void) | null = null;
    const fetchDiskModel = jest.fn();
    const env: IRefreshWatcherEnv = {
      iterDocumentWidgets: () => [{ context: makeContext() }],
      fetchDiskModel,
      setInterval: handler => {
        tickHandler = handler;
        return 'h';
      },
      clearInterval: handle => {
        if (handle === 'h') {
          cleared = true;
        }
      }
    };
    const teardown = attachOpenFileRefreshWatcher({
      env,
      isEnabled: () => true
    });
    teardown();
    expect(cleared).toBe(true);
    // Invoke the captured tick handler directly to confirm any
    // straggling timer fire after clearInterval would still be inert.
    // (clearInterval is best-effort across browsers; the watcher's
    // post-teardown handler should not stat any contexts.)
    tickHandler!();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(fetchDiskModel).not.toHaveBeenCalled();
  });
});
