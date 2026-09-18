// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * A turn streams by rewriting its response message on every delta. Doing that
 * by rebuilding the transcript from a snapshot taken when the turn started
 * reverts anything that changed in the meantime, because the snapshot holds
 * the other messages as they were then. Upserting the turn's own message into
 * current state touches only that message, so a second turn streaming
 * alongside, or a flag set on an earlier message, survives.
 *
 * `afterId` keeps a response next to the prompt it answers. Without it a
 * response that arrives while another turn has already appended its prompt
 * would land at the end of the transcript, under someone else's question.
 *
 * An existing message is merged rather than replaced, because a streaming turn
 * rebuilds only the fields it owns; anything set on the message from outside
 * (a feedback rating, say) would otherwise be dropped by the next delta.
 */
export function upsertMessageById<T extends { id: string }>(
  messages: T[],
  message: T,
  afterId?: string
): T[] {
  const index = messages.findIndex(existing => existing.id === message.id);
  if (index !== -1) {
    const next = messages.slice();
    next[index] = { ...messages[index], ...message };
    return next;
  }
  const anchor =
    afterId === undefined
      ? -1
      : messages.findIndex(existing => existing.id === afterId);
  if (anchor === -1) {
    return [...messages, message];
  }
  const next = messages.slice();
  next.splice(anchor + 1, 0, message);
  return next;
}
