// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// A prompt beginning with @ or / opens the suggestion popover, and the
// popover used to keep claiming Enter after the prompt was typed past every
// match, so such a message could not be sent from the keyboard at all.
import {
  isPrefixPopoverUsable,
  prefixesMatching
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
