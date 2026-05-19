// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  attachOpenFileRefreshWatcher,
  IRefreshWatcherEnv,
  shouldRevertContext
} from '../../src/open-file-refresh-watcher';

describe('shouldRevertContext', () => {
  const base = {
    isDirty: false,
    isReady: true,
    isDisposed: false,
    contextLastModified: '2026-01-01T00:00:00.000000Z',
    diskLastModified: '2026-01-01T00:00:00.000000Z'
  };

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
});

interface IFakeContext {
  path: string;
  contentsModel: { last_modified: string } | null;
  model: { dirty: boolean };
  isReady: boolean;
  isDisposed: boolean;
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
      iterDocumentWidgets: () =>
        widgets as unknown as Iterable<
          import('@jupyterlab/docregistry').DocumentWidget
        >,
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

  it('stops polling when the teardown function is invoked', () => {
    let cleared = false;
    const env: IRefreshWatcherEnv = {
      iterDocumentWidgets: () => [],
      fetchDiskModel: async () => {
        throw new Error('should not be called');
      },
      setInterval: () => 'h',
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
  });
});
