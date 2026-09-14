// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  classifyInlineCompletionResponse,
  IInlineCompletionRequestInfo
} from '../../src/inline-completion-request';

function makeRequest(messageId: string): IInlineCompletionRequestInfo {
  return {
    chatId: `chat-${messageId}`,
    messageId,
    requestTime: new Date('2026-01-01T00:00:00Z')
  };
}

describe('classifyInlineCompletionResponse', () => {
  it('accepts a completion for the request that is still current', () => {
    const request = makeRequest('a');

    expect(classifyInlineCompletionResponse('a', true, request, request)).toBe(
      'accept'
    );
  });

  it('ignores a response addressed to a different request', () => {
    const request = makeRequest('a');

    // Another callback owns this one. Resolving here would hand this
    // request's promise an answer computed for different text.
    expect(classifyInlineCompletionResponse('b', true, request, request)).toBe(
      'ignore'
    );
  });

  it('discards its own response once a newer request has started', () => {
    const request = makeRequest('a');
    const newer = makeRequest('b');

    // The regression this guards: reading the shared field instead of the
    // captured request made a slow answer look like it belonged to whichever
    // request had started most recently.
    expect(classifyInlineCompletionResponse('a', true, request, newer)).toBe(
      'discard'
    );
  });

  it('discards a stream end rather than treating it as a completion', () => {
    const request = makeRequest('a');

    expect(classifyInlineCompletionResponse('a', false, request, request)).toBe(
      'discard'
    );
  });

  it('ignores a foreign response even when superseded', () => {
    const request = makeRequest('a');
    const newer = makeRequest('b');

    // Ownership is checked first on purpose: a callback must never resolve a
    // promise for a message it was not waiting for, superseded or not.
    expect(classifyInlineCompletionResponse('c', true, request, newer)).toBe(
      'ignore'
    );
  });

  it('discards when the provider has no request on record', () => {
    const request = makeRequest('a');

    expect(classifyInlineCompletionResponse('a', true, request, null)).toBe(
      'discard'
    );
  });

  it('distinguishes requests by identity, not by equal field values', () => {
    const request = makeRequest('a');
    const lookalike = makeRequest('a');

    // Two requests can carry the same id only through a bug, but identity
    // comparison is what the provider relies on, so pin it.
    expect(
      classifyInlineCompletionResponse('a', true, request, lookalike)
    ).toBe('discard');
  });
});
