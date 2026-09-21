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

/**
 * Keep the keyboard-selected suggestion visible.
 *
 * The popover scrolls when it has more suggestions than fit, but arrow keys
 * only move the selection, so it would walk out of view. `block: 'nearest'`
 * scrolls the list by the smallest amount and leaves it alone when the row is
 * already visible.
 */
export function scrollSelectedSuggestionIntoView(
  popover: HTMLElement | null,
  index: number
): void {
  const item = popover?.children[index] as HTMLElement | undefined;
  item?.scrollIntoView?.({ block: 'nearest' });
}
