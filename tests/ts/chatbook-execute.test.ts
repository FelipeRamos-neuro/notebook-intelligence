// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Drives the patched `CodeCell.execute` and the `nbi_chatbook_code` payload
 * handler in `chatbook.ts` against a model of JupyterLab's execution chain:
 *
 * - `OutputArea`'s `future` setter disposes the previous future, which rejects
 *   its `done` with "Canceled future for execute_request message before
 *   replies were done" (@jupyterlab/outputarea, @jupyterlab/services).
 * - `runCell` in @jupyterlab/notebook treats a "Canceled" rejection as
 *   `ran = false` and does not report the cell as executed.
 * - Run All sends every cell's execute_request at once (`Promise.all`), so the
 *   kernel serves requests in the order they were sent.
 *
 * The fake kernel publishes the payload before its execute_reply, as the real
 * Chatbook kernel does, and executes only when its own policy says so or when
 * the request carries `approvedCode` equal to what it generated.
 */

const cfg: {
  chatbookExecutionMode: string;
  chatbookHasContextProviders: boolean;
  chatbookHasGuidelines: boolean;
  chatbookLlmDangerScan: boolean;
  chatbookBackendKernel: string;
} = {
  chatbookExecutionMode: 'always-confirm',
  chatbookHasContextProviders: false,
  chatbookHasGuidelines: false,
  chatbookLlmDangerScan: false,
  chatbookBackendKernel: ''
};

jest.mock(
  '@jupyterlab/cells',
  () => {
    class CodeCell {
      static execute(
        cell: unknown,
        sessionContext: unknown,
        metadata: unknown
      ) {
        return (globalThis as any).__fakeJupyterExecute(
          cell,
          sessionContext,
          metadata
        );
      }
    }
    return { CodeCell };
  },
  { virtual: true }
);
jest.mock('@jupyterlab/codemirror', () => ({}), { virtual: true });
jest.mock('@codemirror/view', () => ({}), { virtual: true });
jest.mock('@jupyterlab/notebook', () => ({ NotebookPanel: class {} }), {
  virtual: true
});
jest.mock('@jupyterlab/services', () => ({}), { virtual: true });
jest.mock('../../src/api', () => ({
  NBIAPI: {
    get config() {
      return cfg;
    },
    configChanged: { connect: () => undefined }
  }
}));
jest.mock('../../src/chatbook-mentions', () => ({
  setChatbookMentionsEnabled: () => undefined
}));
jest.mock('../../src/utils', () => ({ cellOutputAsText: () => '' }));
jest.mock('../../src/notebook-kernels', () => ({
  sharedKernelSpecManager: () => ({
    ready: Promise.resolve(),
    specs: { kernelspecs: {} }
  }),
  resolveChatbookBackendProfile: () => ({
    language: 'python',
    displayName: 'Python'
  }),
  mimeTypeForNotebookLanguage: () => 'text/x-python'
}));

import { CodeCell } from '@jupyterlab/cells';
import {
  attachChatbookNotebooks,
  patchCodeCellExecute
} from '../../src/chatbook';
import { sha256Hex } from '../../src/chatbook-core';

type Listener = (sender: unknown, args: unknown) => void;

function signal() {
  const listeners: Listener[] = [];
  return {
    connect: (fn: Listener) => {
      listeners.push(fn);
      return true;
    },
    disconnect: (fn: Listener) => {
      const index = listeners.indexOf(fn);
      if (index >= 0) {
        listeners.splice(index, 1);
      }
      return true;
    },
    emit: (sender: unknown, args: unknown) => {
      for (const fn of [...listeners]) {
        fn(sender, args);
      }
    }
  };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

interface IFuture {
  done: Promise<unknown>;
  resolve: (reply: unknown) => void;
  reject: (reason: Error) => void;
  isDone: boolean;
  dispose: () => void;
}

function makeFuture(): IFuture {
  let resolve!: (reply: unknown) => void;
  let reject!: (reason: Error) => void;
  const done = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  done.catch(() => undefined);
  const future: IFuture = {
    done,
    isDone: false,
    resolve: reply => {
      future.isDone = true;
      resolve(reply);
    },
    reject: reason => {
      future.isDone = true;
      reject(reason);
    },
    dispose: () => {
      if (!future.isDone) {
        future.reject(
          new Error(
            'Canceled future for execute_request message before replies were done'
          )
        );
      }
    }
  };
  return future;
}

interface IRequest {
  cell: any;
  code: string;
  meta: Record<string, any>;
  future: IFuture;
}

/** One fake Chatbook kernel per test: FIFO shell queue, IOPub before reply. */
class FakeKernel {
  policy = 'always-confirm';
  level: 'clean' | 'risky' = 'clean';
  omitExecutedField = false;
  failCodeExecute = false;
  generate: (prompt: string) => string = prompt => `print(${prompt})`;
  log: string[] = [];
  anyMessage = signal();
  name = 'chatbook';
  private queue: IRequest[] = [];
  private draining = false;
  private executionCount = 0;

  enqueue(request: IRequest): void {
    this.queue.push(request);
    void this.drain();
  }

  private async drain(): Promise<void> {
    if (this.draining) {
      return;
    }
    this.draining = true;
    while (this.queue.length) {
      const request = this.queue.shift()!;
      await tick();
      await this.serve(request);
    }
    this.draining = false;
  }

  private async serve(request: IRequest): Promise<void> {
    const meta = request.meta;
    if (meta.executeMode === 'code') {
      const code = meta.codeSource || request.code;
      this.log.push(`exec:${code}`);
      await tick();
      if (this.failCodeExecute) {
        // A child kernel that dies mid-run: the wrapper reports the failure
        // and no count, because nothing of this request ran.
        request.future.resolve({
          content: {
            status: 'error',
            ename: 'RuntimeError',
            evalue: 'backend kernel died',
            traceback: [],
            execution_count: null
          }
        });
        return;
      }
      request.future.resolve({
        content: { status: 'ok', execution_count: ++this.executionCount }
      });
      return;
    }
    const cacheHit =
      typeof meta.cachedCode === 'string' && meta.cachedCode.length > 0;
    const code = cacheHit ? meta.cachedCode : this.generate(request.code);
    this.log.push(`${cacheHit ? 'cache' : 'gen'}:${code}`);
    const policyRuns =
      this.policy === 'auto-run' ||
      (this.policy === 'confirm-if-risky' && this.level === 'clean');
    const approved =
      typeof meta.approvedCode === 'string' && meta.approvedCode === code;
    const willExecute = Boolean(code) && (policyRuns || approved);
    const payload: Record<string, unknown> = {
      cellId: meta.cellId,
      generatedCode: code,
      promptHash: meta.promptHash,
      dangerLevel: this.level,
      dangerReasons: this.level === 'risky' ? ['risky import'] : [],
      cacheHit,
      executed: willExecute,
      executionPolicy: this.policy
    };
    if (this.omitExecutedField) {
      delete payload.executed;
    }
    this.anyMessage.emit(this, {
      direction: 'recv',
      msg: { header: { msg_type: 'nbi_chatbook_code' }, content: payload }
    });
    await tick();
    if (willExecute) {
      this.log.push(`exec:${code}`);
    }
    if (!request.future.isDone) {
      request.future.resolve({
        content: {
          status: 'ok',
          execution_count: willExecute ? ++this.executionCount : null
        }
      });
    }
  }
}

function makeCell(source: string) {
  const prompts: string[] = [];
  const model: any = {
    id: `cell-${Math.random().toString(36).slice(2)}`,
    type: 'code',
    mimeType: '',
    metadata: {},
    setMetadata(key: string, value: unknown) {
      this.metadata = { ...this.metadata, [key]: value };
    },
    sharedModel: {
      getSource: () => source,
      setSource: (value: string) => {
        source = value;
      }
    }
  };
  return {
    model,
    node: document.createElement('div'),
    parent: null,
    outputArea: { future: null as IFuture | null },
    isDisposed: false,
    prompts,
    setPrompt(value: string) {
      prompts.push(value);
    }
  };
}

function makeNotebook(kernel: FakeKernel, cells: any[]) {
  const sessionContext: any = {
    session: { kernel },
    kernelPreference: { name: 'chatbook' },
    kernelChanged: signal(),
    ready: Promise.resolve()
  };
  const panel: any = {
    isDisposed: false,
    sessionContext,
    context: { path: 'nb.ipynb', ready: Promise.resolve() },
    content: {
      widgets: cells,
      activeCell: cells[0],
      activeCellChanged: signal()
    },
    model: { contentChanged: signal() },
    disposed: signal()
  };
  attachChatbookNotebooks({
    widgetAdded: signal(),
    forEach: (fn: (panel: unknown) => void) => fn(panel)
  } as any);
  return { panel, sessionContext };
}

/** Mirrors `runCell` in @jupyterlab/notebook/lib/cellexecutor.js. */
async function runCell(
  cell: any,
  sessionContext: unknown,
  executed: any[]
): Promise<boolean> {
  let ran = false;
  try {
    const reply: any = await CodeCell.execute(
      cell,
      sessionContext as any,
      {
        deletedCells: [],
        recordTiming: false
      } as any
    );
    ran = (() => {
      if (!reply) {
        return true;
      }
      if (reply.content.status === 'ok') {
        return true;
      }
      throw new Error('KernelReplyNotOK');
    })();
  } catch (reason) {
    if ((reason as Error).message.startsWith('Canceled')) {
      ran = false;
    } else {
      throw reason;
    }
  }
  if (ran) {
    executed.push(cell);
  }
  return ran;
}

function confirmBar(cell: any): HTMLElement | null {
  return cell.node.querySelector('.nbi-chatbook-confirm');
}

async function clickRun(cell: any): Promise<void> {
  const button = cell.node.querySelector(
    '.nbi-chatbook-confirm-run'
  ) as HTMLButtonElement | null;
  expect(button).not.toBeNull();
  button!.click();
  await settle();
}

async function settle(): Promise<void> {
  for (let i = 0; i < 12; i += 1) {
    await tick();
  }
}

let kernel: FakeKernel;
let executed: any[];

beforeAll(() => {
  (globalThis as any).__fakeJupyterExecute = async (
    cell: any,
    sessionContext: any,
    metadata: any
  ) => {
    const code = cell.model.sharedModel.getSource();
    if (!code.trim() || !sessionContext.session?.kernel) {
      return undefined;
    }
    const previous = cell.outputArea.future;
    const future = makeFuture();
    if (previous) {
      previous.dispose();
    }
    cell.outputArea.future = future;
    // JupyterLab marks the cell running before the reply arrives; the prompt
    // assertions below are about what happens to that marker.
    cell.setPrompt('*');
    sessionContext.session.kernel.enqueue({
      cell,
      code,
      meta: metadata?.nbi_chatbook || {},
      future
    });
    return future.done;
  };
  patchCodeCellExecute();
});

beforeEach(() => {
  cfg.chatbookExecutionMode = 'always-confirm';
  cfg.chatbookHasContextProviders = false;
  cfg.chatbookHasGuidelines = false;
  kernel = new FakeKernel();
  executed = [];
});

describe('chatbook execute: approved code re-run', () => {
  it('re-runs approved code inside the original request, so the cell counts as executed', async () => {
    cfg.chatbookHasGuidelines = true;
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);

    expect(await runCell(cell, sessionContext, executed)).toBe(true);
    expect(confirmBar(cell)).not.toBeNull();
    await clickRun(cell);
    expect(kernel.log).toEqual([
      'gen:print(plot the values)',
      'exec:print(plot the values)'
    ]);

    kernel.log.length = 0;
    const ran = await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual([
      'gen:print(plot the values)',
      'exec:print(plot the values)'
    ]);
    expect(confirmBar(cell)).toBeNull();
    // JupyterLab must see the run as executed: no cancelled future, and
    // `onCellExecuted` reported for it.
    expect(ran).toBe(true);
    expect(executed).toHaveLength(2);
  });

  it('keeps Run All in cell order when every cell re-runs approved code', async () => {
    cfg.chatbookHasGuidelines = true;
    const first = makeCell('first');
    const second = makeCell('second');
    const { sessionContext } = makeNotebook(kernel, [first, second]);
    await runCell(first, sessionContext, executed);
    await clickRun(first);
    await runCell(second, sessionContext, executed);
    await clickRun(second);

    kernel.log.length = 0;
    executed.length = 0;
    const results = await Promise.all([
      runCell(first, sessionContext, executed),
      runCell(second, sessionContext, executed)
    ]);
    await settle();
    expect(results).toEqual([true, true]);
    expect(kernel.log).toEqual([
      'gen:print(first)',
      'exec:print(first)',
      'gen:print(second)',
      'exec:print(second)'
    ]);
    expect(executed).toEqual([first, second]);
  });

  it('sends Run All requests in notebook order when a Cd cell follows a prompt', async () => {
    const prompt = makeCell('load the data');
    const code = makeCell('df.head()');
    code.model.metadata = {
      nbi: { chatbook: { mode: 'code', origin: 'code' } }
    };
    const { sessionContext } = makeNotebook(kernel, [prompt, code]);
    const results = await Promise.all([
      runCell(prompt, sessionContext, executed),
      runCell(code, sessionContext, executed)
    ]);
    await settle();
    expect(results).toEqual([true, true]);
    expect(kernel.log).toEqual(['gen:print(load the data)', 'exec:df.head()']);

    // Second pass: the prompt cell now takes the session cache (one fewer
    // await than a fresh generation) and must still go first.
    await clickRun(prompt);
    kernel.log.length = 0;
    await Promise.all([
      runCell(prompt, sessionContext, executed),
      runCell(code, sessionContext, executed)
    ]);
    await settle();
    expect(kernel.log).toEqual(['exec:print(load the data)', 'exec:df.head()']);
  });

  it('asks again when regeneration returns different code', async () => {
    cfg.chatbookHasGuidelines = true;
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await clickRun(cell);

    kernel.generate = () => 'print("changed")';
    kernel.log.length = 0;
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual(['gen:print("changed")']);
    expect(confirmBar(cell)).not.toBeNull();
  });
});

describe('chatbook execute: kernel policy is authoritative', () => {
  it('never runs code the kernel declined when nothing was approved', async () => {
    // Frontend still believes auto-run; the kernel was capped to
    // always-confirm out of band.
    cfg.chatbookExecutionMode = 'auto-run';
    kernel.policy = 'always-confirm';
    const cell = makeCell('delete everything');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual(['gen:print(delete everything)']);
    expect(confirmBar(cell)).not.toBeNull();
    expect(
      confirmBar(cell)!.querySelector('.nbi-chatbook-confirm-hint')!.textContent
    ).toContain('confirm every natural-language run');
  });

  it('does not run auto-run code twice when the payload has no executed flag', async () => {
    cfg.chatbookExecutionMode = 'auto-run';
    kernel.policy = 'auto-run';
    kernel.omitExecutedField = true;
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual([
      'gen:print(plot the values)',
      'exec:print(plot the values)'
    ]);
    expect(confirmBar(cell)).toBeNull();
  });

  it('runs once under auto-run and takes the session cache on the next run', async () => {
    cfg.chatbookExecutionMode = 'auto-run';
    kernel.policy = 'auto-run';
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual([
      'gen:print(plot the values)',
      'exec:print(plot the values)'
    ]);
    kernel.log.length = 0;
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual(['exec:print(plot the values)']);
  });

  it('runs once under a clean confirm-if-risky scan', async () => {
    cfg.chatbookExecutionMode = 'confirm-if-risky';
    kernel.policy = 'confirm-if-risky';
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual([
      'gen:print(plot the values)',
      'exec:print(plot the values)'
    ]);
    expect(confirmBar(cell)).toBeNull();
  });

  it('confirms a risky scan under confirm-if-risky, then re-runs it approved', async () => {
    cfg.chatbookExecutionMode = 'confirm-if-risky';
    cfg.chatbookHasGuidelines = true;
    kernel.policy = 'confirm-if-risky';
    kernel.level = 'risky';
    const cell = makeCell('remove the file');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual(['gen:print(remove the file)']);
    await clickRun(cell);
    kernel.log.length = 0;
    const ran = await runCell(cell, sessionContext, executed);
    await settle();
    expect(ran).toBe(true);
    expect(kernel.log).toEqual([
      'gen:print(remove the file)',
      'exec:print(remove the file)'
    ]);
    expect(confirmBar(cell)).toBeNull();
  });
});

describe('chatbook execute: unanswered confirm bar', () => {
  it('does not let the session cache run code the user never approved', async () => {
    cfg.chatbookHasGuidelines = true;
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await clickRun(cell);

    // Regeneration produces different code; the bar opens and is ignored.
    kernel.generate = () => 'print("B")';
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(confirmBar(cell)).not.toBeNull();

    // Guidelines go away, so the session-cache fast path becomes available.
    cfg.chatbookHasGuidelines = false;
    kernel.log.length = 0;
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).not.toContain('exec:print("B")');
    expect(kernel.log[0]).toMatch(/^(gen|cache):/);
    expect(confirmBar(cell)).not.toBeNull();
  });

  it('takes the session cache for code the user did approve', async () => {
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await clickRun(cell);
    kernel.log.length = 0;
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(kernel.log).toEqual(['exec:print(plot the values)']);
    expect(confirmBar(cell)).toBeNull();
  });

  it("clears stored code on Don't run", async () => {
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    expect(cell.model.metadata.nbi.chatbook.generatedCode).toBe(
      'print(plot the values)'
    );
    (
      cell.node.querySelector(
        '.nbi-chatbook-confirm-discard'
      ) as HTMLButtonElement
    ).click();
    expect(cell.model.metadata.nbi.chatbook.generatedCode).toBeUndefined();
    expect(cell.model.metadata.nbi.chatbook.promptHash).toBeUndefined();
    expect(cell.model.metadata.nbi.chatbook.prompt).toBe('plot the values');
  });
});

describe('chatbook execute: the running marker', () => {
  it('clears the running marker when nothing ran', async () => {
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);

    await runCell(cell, sessionContext, executed);
    await settle();

    // JupyterLab painted the running marker before the request and repaints
    // the prompt only on a count change, so a reply with no count needs this
    // or the cell claims to be running while the bar waits for an answer.
    expect(confirmBar(cell)).not.toBeNull();
    expect(cell.prompts[cell.prompts.length - 1]).toBe('');
  });

  it('leaves the prompt alone when the code did run', async () => {
    cfg.chatbookExecutionMode = 'auto-run';
    kernel.policy = 'auto-run';
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);

    await runCell(cell, sessionContext, executed);
    await settle();

    // The reply carries the child kernel's number, so JupyterLab paints the
    // prompt itself and nothing here should interfere.
    expect(kernel.log).toContain('exec:print(plot the values)');
    expect(cell.prompts).not.toContain('');
  });

  it('clears the running marker when approved code fails to run', async () => {
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    await runCell(cell, sessionContext, executed);
    await settle();
    cell.prompts.length = 0;

    // The confirm bar runs code through CodeCell.execute directly, so the
    // marker has to come off here: nothing in Run All is watching this one.
    kernel.failCodeExecute = true;
    await clickRun(cell);
    await settle();

    expect(cell.prompts[cell.prompts.length - 1]).toBe('');
  });
});

describe('chatbook execute: request metadata', () => {
  it('sends the approved code with the next generate request', async () => {
    cfg.chatbookHasGuidelines = true;
    const cell = makeCell('plot the values');
    const { sessionContext } = makeNotebook(kernel, [cell]);
    const seen: Record<string, any>[] = [];
    const original = kernel.enqueue.bind(kernel);
    kernel.enqueue = request => {
      seen.push(request.meta);
      original(request);
    };
    await runCell(cell, sessionContext, executed);
    expect(seen[0].approvedCode).toBeUndefined();
    await clickRun(cell);
    await runCell(cell, sessionContext, executed);
    await settle();
    const hash = await sha256Hex('plot the values');
    expect(seen[2].executeMode).toBe('prompt');
    expect(seen[2].promptHash).toBe(hash);
    expect(seen[2].approvedCode).toBe('print(plot the values)');
  });
});
