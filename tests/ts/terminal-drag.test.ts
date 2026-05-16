// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { attachTerminalDragDrop } from '../../src/terminal-drag';
import { formatForMode, invertMode } from '../../src/terminal-drag-format';

describe('formatForMode', () => {
  it('prefixes each path with @ in mention mode', () => {
    expect(formatForMode(['/tmp/a.txt', '/tmp/b.txt'], 'mention')).toBe(
      '@/tmp/a.txt @/tmp/b.txt'
    );
  });

  it('shell-escapes each path in raw mode', () => {
    expect(formatForMode(['/tmp/a.txt', '/tmp/with space.txt'], 'raw')).toBe(
      "'/tmp/a.txt' '/tmp/with space.txt'"
    );
  });

  it('returns empty string for empty input in either mode', () => {
    expect(formatForMode([], 'mention')).toBe('');
    expect(formatForMode([], 'raw')).toBe('');
  });

  it('handles a single path in mention mode', () => {
    expect(formatForMode(['/tmp/only.txt'], 'mention')).toBe('@/tmp/only.txt');
  });

  it('does not quote the @-prefix in mention mode', () => {
    // Intentional: Claude Code parses bare @<path> tokens, so wrapping them
    // in shell quotes would break the parse. Mention mode trusts the path
    // not to contain shell metacharacters; raw mode is the path that quotes.
    expect(formatForMode(['/tmp/a b.txt'], 'mention')).toBe('@/tmp/a b.txt');
  });
});

describe('invertMode', () => {
  it('returns the original mode when shouldInvert is false', () => {
    expect(invertMode('mention', false)).toBe('mention');
    expect(invertMode('raw', false)).toBe('raw');
  });

  it('flips mention to raw and back when shouldInvert is true', () => {
    expect(invertMode('mention', true)).toBe('raw');
    expect(invertMode('raw', true)).toBe('mention');
  });
});

describe('attachTerminalDragDrop lm-drop handler', () => {
  type ConnectSlot = (sender: unknown, widget: unknown) => void;
  type DisposedSlot = () => void;

  interface ITestWidgetEnv {
    mock: any;
    host: HTMLElement;
    paste: jest.Mock;
    activate: jest.Mock;
    fireDisposed: () => void;
  }

  function setupTracker(): {
    tracker: any;
    fireWidgetAdded: (widget: unknown) => void;
  } {
    let widgetAddedSlot: ConnectSlot | null = null;
    const tracker = {
      widgetAdded: {
        connect: (slot: ConnectSlot) => {
          widgetAddedSlot = slot;
        }
      },
      forEach: (_fn: (widget: unknown) => void) => undefined
    };
    return {
      tracker,
      fireWidgetAdded: (widget: unknown) => {
        if (!widgetAddedSlot) {
          throw new Error('widgetAdded was never wired');
        }
        widgetAddedSlot(null, widget);
      }
    };
  }

  function buildWidget(): ITestWidgetEnv {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const paste = jest.fn();
    const activate = jest.fn();
    let isDisposed = false;
    const disposedSlots: DisposedSlot[] = [];
    const mock = {
      node: host,
      content: { paste },
      toolbar: { addItem: jest.fn().mockReturnValue(true) },
      disposed: {
        connect: (slot: DisposedSlot) => {
          disposedSlots.push(slot);
        }
      },
      activate,
      get isDisposed() {
        return isDisposed;
      }
    };
    return {
      mock,
      host,
      paste,
      activate,
      fireDisposed: () => {
        isDisposed = true;
        disposedSlots.forEach(slot => slot());
      }
    };
  }

  function dispatchLmDrop(target: EventTarget, paths: string[]): void {
    const event: any = new Event('lm-drop', {
      bubbles: true,
      cancelable: true
    });
    event.mimeData = {
      hasData: (key: string) => key === 'application/x-jupyter-icontents',
      getData: (key: string) =>
        key === 'application/x-jupyter-icontents' ? paths : null
    };
    event.proposedAction = 'move';
    event.dropAction = 'none';
    event.shiftKey = false;
    target.dispatchEvent(event);
  }

  // Each test must dispose its widget so the document-level lm-* listeners
  // attached by setupTerminal don't leak across tests in this file.
  const envs: ITestWidgetEnv[] = [];
  afterEach(() => {
    envs.splice(0).forEach(env => {
      env.fireDisposed();
      if (env.host.parentNode) {
        env.host.parentNode.removeChild(env.host);
      }
    });
  });

  function newEnv(): ITestWidgetEnv {
    const env = buildWidget();
    envs.push(env);
    return env;
  }

  it('activates the widget after pasting so the next Enter goes to xterm, not the file-browser', () => {
    const env = newEnv();
    const { tracker, fireWidgetAdded } = setupTracker();
    attachTerminalDragDrop({ tracker, isEnabled: () => true });
    fireWidgetAdded(env.mock);

    dispatchLmDrop(env.host, ['/tmp/file.txt']);

    expect(env.paste).toHaveBeenCalledTimes(1);
    expect(env.paste).toHaveBeenCalledWith('@/tmp/file.txt ');
    expect(env.activate).toHaveBeenCalledTimes(1);
    // Activate must run after paste so the terminal owns focus before the
    // user presses Enter; otherwise the file-browser's "open selected item"
    // handler eats the first keypress.
    expect(env.activate.mock.invocationCallOrder[0]).toBeGreaterThan(
      env.paste.mock.invocationCallOrder[0]
    );
  });

  it('handles drops on a descendant of the terminal host, not just the host itself', () => {
    const env = newEnv();
    const inner = document.createElement('div');
    env.host.appendChild(inner);
    const { tracker, fireWidgetAdded } = setupTracker();
    attachTerminalDragDrop({ tracker, isEnabled: () => true });
    fireWidgetAdded(env.mock);

    dispatchLmDrop(inner, ['/tmp/nested.txt']);

    expect(env.paste).toHaveBeenCalledWith('@/tmp/nested.txt ');
    expect(env.activate).toHaveBeenCalled();
  });
});
