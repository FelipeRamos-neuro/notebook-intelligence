// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// A prompt beginning with @ or / opens the suggestion popover, and the
// popover used to keep claiming Enter after the prompt was typed past every
// match, so such a message could not be sent from the keyboard at all.
import {
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
