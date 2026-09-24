// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// A prompt beginning with @ or / opens the suggestion popover, and the
// popover used to keep claiming Enter after the prompt was typed past every
// match, so such a message could not be sent from the keyboard at all.
import {
  activeSuggestionIndex,
  isPrefixPopoverUsable,
  prefixesMatching,
  scrollSelectedSuggestionIntoView
} from '../../src/chat-prefix-suggestions';

const PREFIXES = ['@mcp', '/clear', '/newNotebook', '/newPythonFile'];

describe('prefixesMatching', () => {
  it('offers everything for an empty prompt', () => {
    expect(prefixesMatching(PREFIXES, '')).toEqual(PREFIXES);
    expect(prefixesMatching(PREFIXES, '   ')).toEqual(PREFIXES);
  });

  it('narrows to substring matches', () => {
    expect(prefixesMatching(PREFIXES, '/new')).toEqual([
      '/newNotebook',
      '/newPythonFile'
    ]);
    expect(prefixesMatching(PREFIXES, '@')).toEqual(['@mcp']);
  });

  it('offers nothing once the prompt is typed past every match', () => {
    expect(prefixesMatching(PREFIXES, '@Ada please review this')).toEqual([]);
    expect(prefixesMatching(PREFIXES, '/usr/bin/python is missing')).toEqual(
      []
    );
  });

  it('does not mutate the list it was given', () => {
    const original = [...PREFIXES];
    prefixesMatching(PREFIXES, '').push('/mutated');
    expect(PREFIXES).toEqual(original);
  });
});

describe('isPrefixPopoverUsable', () => {
  it('is false with nothing to suggest, even while open', () => {
    // The case behind the bug: the popover renders nothing here, so it must
    // not claim Enter or Tab either.
    expect(isPrefixPopoverUsable(true, [])).toBe(false);
  });

  it('is true while it has something to offer', () => {
    expect(isPrefixPopoverUsable(true, ['/clear'])).toBe(true);
  });

  it('is false when closed', () => {
    expect(isPrefixPopoverUsable(false, ['/clear'])).toBe(false);
  });
});

describe('scrollSelectedSuggestionIntoView', () => {
  // jsdom does not implement scrollIntoView, so each row gets a spy.
  function popoverWithRows(count: number) {
    const popover = document.createElement('div');
    const spies: jest.Mock[] = [];
    for (let i = 0; i < count; i++) {
      const row = document.createElement('div');
      const spy = jest.fn();
      row.scrollIntoView = spy;
      spies.push(spy);
      popover.appendChild(row);
    }
    return { popover, spies };
  }

  it('scrolls only the selected row, by the smallest amount', () => {
    const { popover, spies } = popoverWithRows(30);
    scrollSelectedSuggestionIntoView(popover, 17);
    expect(spies[17]).toHaveBeenCalledWith({ block: 'nearest' });
    spies.forEach((spy, i) => {
      if (i !== 17) {
        expect(spy).not.toHaveBeenCalled();
      }
    });
  });

  it('follows the selection when it wraps from the first row to the last', () => {
    const { popover, spies } = popoverWithRows(30);
    scrollSelectedSuggestionIntoView(popover, 29);
    expect(spies[29]).toHaveBeenCalledTimes(1);
  });

  it('ignores a missing popover, an out-of-range index, or no scrollIntoView', () => {
    const { popover } = popoverWithRows(2);
    expect(() => scrollSelectedSuggestionIntoView(null, 0)).not.toThrow();
    expect(() => scrollSelectedSuggestionIntoView(popover, 5)).not.toThrow();
    (popover.children[0] as any).scrollIntoView = undefined;
    expect(() => scrollSelectedSuggestionIntoView(popover, 0)).not.toThrow();
  });
});

describe('activeSuggestionIndex', () => {
  const COMMANDS = Array.from({ length: 30 }, (_, i) => `/command${i}`);

  it('keeps an index that still points at a row', () => {
    expect(activeSuggestionIndex(0, 30)).toBe(0);
    expect(activeSuggestionIndex(12, 30)).toBe(12);
    expect(activeSuggestionIndex(29, 30)).toBe(29);
  });

  it('falls back to the first row when typing narrowed the list past the index', () => {
    // Arrow down to row 12 of 30, then type a character that leaves 3 matches.
    const narrowed = prefixesMatching(COMMANDS, '/command1');
    expect(narrowed.length).toBeLessThan(13);
    // Read as-is the stale index selects nothing, and Enter would then apply
    // `undefined`.
    expect(narrowed[12]).toBeUndefined();
    const index = activeSuggestionIndex(12, narrowed.length);
    expect(narrowed[index]).toBeDefined();
    expect(index).toBe(0);
  });

  it('treats a negative or out-of-range index as the first row', () => {
    expect(activeSuggestionIndex(-1, 5)).toBe(0);
    expect(activeSuggestionIndex(5, 5)).toBe(0);
  });

  it('returns 0 for an empty list, where the popover claims no keys anyway', () => {
    expect(activeSuggestionIndex(3, 0)).toBe(0);
    expect(isPrefixPopoverUsable(true, [])).toBe(false);
  });

  it('lets ArrowDown/ArrowUp wrap from a valid row after the list shrinks', () => {
    const narrowed = ['/command1', '/command10', '/command11'];
    const active = activeSuggestionIndex(12, narrowed.length);
    // Same arithmetic as the key handlers in chat-sidebar.tsx.
    const down = (active + 1 + narrowed.length) % narrowed.length;
    const up = (active - 1 + narrowed.length) % narrowed.length;
    expect(narrowed[down]).toBeDefined();
    expect(narrowed[up]).toBe('/command11');
  });
});
