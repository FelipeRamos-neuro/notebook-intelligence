// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export interface IRegisterInFlightOptions {
  /**
   * A hidden turn (background notebook generation) renders no transcript
   * message and offers no Stop button, so the sidebar's Stop must not reach
   * it. Registering one would let a visible turn's Stop kill it silently.
   */
  hideInChat?: boolean;
}

/**
 * Records a turn as cancellable. Only turns that can actually be stopped from
 * the sidebar belong here, and only real ids: '' is the idle sentinel
 * elsewhere in the sidebar and the backend keys no handler under it.
 */
export function registerInFlightTurn(
  messageIds: Set<string>,
  messageId: string,
  options: IRegisterInFlightOptions = {}
): boolean {
  if (messageId === '' || options.hideInChat) {
    return false;
  }
  messageIds.add(messageId);
  return true;
}

/**
 * The backend matches a cancellation by the websocket message id of the
 * request it belongs to and ignores an id it has no handler for, so cancelling
 * has to name every turn that is still running. A single "last message id"
 * only ever named the turn the sidebar input started, which left a turn
 * started by an editor command running with no way to stop it.
 *
 * `send` is called once per id, and the set is emptied, so a second press
 * cannot re-cancel turns that are already gone.
 */
export function cancelInFlightTurns(
  messageIds: Set<string>,
  send: (messageId: string) => void
): string[] {
  const cancelled = [...messageIds];
  // Emptied before sending so a send that fails cannot leave ids behind for a
  // later press to cancel a second time.
  messageIds.clear();
  cancelled.forEach(send);
  return cancelled;
}
