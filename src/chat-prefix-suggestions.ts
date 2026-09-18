// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * The slash command and participant suggestions matching what has been typed.
 *
 * An empty prompt offers everything; anything else is a substring match, so
 * typing past the last match legitimately leaves nothing to suggest.
 */
export function prefixesMatching(prefixes: string[], prompt: string): string[] {
  const userInput = prompt.trimStart();
  if (userInput === '') {
    return [...prefixes];
  }
  return prefixes.filter(prefix => prefix.includes(userInput));
}

/**
 * Whether the suggestion popover should act on a keystroke.
 *
 * The popover is opened by typing `@` or `/` and stays open while the prompt
 * is edited, so it can end up with nothing to offer. It renders nothing in
 * that state, and it must not claim Enter or Tab either: a popover that
 * accepts a suggestion it does not have leaves the message unsendable.
 */
export function isPrefixPopoverUsable(
  showPopover: boolean,
  suggestions: string[]
): boolean {
  return showPopover && suggestions.length > 0;
}
