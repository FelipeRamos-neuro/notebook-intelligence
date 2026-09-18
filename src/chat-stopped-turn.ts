// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Stopping a response leaves whatever had streamed so far in place, which on
 * its own is indistinguishable from a model that answered with nothing or
 * failed. The turn's response message is marked instead, so the transcript can
 * say the user stopped it.
 *
 * Whether that message exists yet depends on timing: it is created by the
 * first delta, and a Claude or ACP turn pays session startup before that, so
 * stopping during the wait is ordinary rather than a race. `makeMessage` then
 * supplies a message carrying the marker alone, so the stop is still recorded.
 *
 * The whole transform lives here rather than in the caller so that the case
 * where the turn is already marked cannot be confused with the case where it
 * is absent. Appending on the former would put two messages in the transcript
 * under one id.
 */
export function recordStoppedTurn<T extends { id: string; stopped?: boolean }>(
  messages: T[],
  responseId: string,
  makeMessage: (id: string) => T
): T[] {
  if (responseId === '') {
    return messages;
  }
  let found = false;
  let marked = false;
  const next = messages.map(message => {
    if (message.id !== responseId) {
      return message;
    }
    found = true;
    if (message.stopped) {
      return message;
    }
    marked = true;
    return { ...message, stopped: true };
  });
  if (marked) {
    return next;
  }
  // Already marked: nothing to change, and nothing to add either.
  if (found) {
    return messages;
  }
  return [...messages, makeMessage(responseId)];
}
