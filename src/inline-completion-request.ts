// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Identity bookkeeping for an in-flight inline completion request.
 *
 * The provider issues one request per keystroke burst and keeps the most
 * recent one in a single field, so a response callback cannot ask "is this
 * mine?" by reading that field: by the time a slow answer arrives, the field
 * may describe a request that started later. Comparing against the request
 * captured when the callback was created is what makes the question
 * answerable, and this module exists so that decision can be tested without
 * standing up the extension.
 */

export interface IInlineCompletionRequestInfo {
  chatId: string;
  messageId: string;
  requestTime: Date;
}

/**
 * What to do with a response that arrived for an inline completion request.
 *
 * - `ignore`: not this request's response, so another callback owns it and
 *   this one must not resolve on its behalf.
 * - `discard`: this request's response, but no longer wanted. Resolve with
 *   no items rather than rejecting, because the completer logs a rejected
 *   provider promise as a warning and there is nothing here to warn about.
 * - `accept`: this request's response, still current, carrying a completion.
 */
export type InlineCompletionResponseAction = 'ignore' | 'discard' | 'accept';

/**
 * Decide how a response relates to the request that is waiting for it.
 *
 * `latestRequestInfo` is the provider's shared field. When it no longer
 * points at `requestInfo`, the user has typed since this request went out
 * and the answer describes text they have moved past, so offering it as a
 * suggestion would be offering a completion for content that is gone.
 */
export function classifyInlineCompletionResponse(
  responseId: string,
  isStreamMessage: boolean,
  requestInfo: IInlineCompletionRequestInfo,
  latestRequestInfo: IInlineCompletionRequestInfo | null
): InlineCompletionResponseAction {
  if (responseId !== requestInfo.messageId) {
    return 'ignore';
  }

  if (latestRequestInfo !== requestInfo) {
    return 'discard';
  }

  // A stream end, or a cancellation acknowledgement, rather than a
  // completion payload.
  if (!isStreamMessage) {
    return 'discard';
  }

  return 'accept';
}
