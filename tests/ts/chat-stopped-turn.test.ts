// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stopping a response used to leave the transcript untouched, so a stopped
// turn was indistinguishable from a model that answered with nothing.
import {
  recordStoppedTurn,
  restoreStoppedMarkers
} from '../../src/chat-stopped-turn';

interface ITestMessage {
  id: string;
  from: string;
  contents: string[];
  stopped?: boolean;
}

const transcript = (): ITestMessage[] => [
  { id: 'u1', from: 'user', contents: ['hello'] },
  { id: 'r1', from: 'copilot', contents: ['partial answer'] }
];

const placeholder = (id: string): ITestMessage => ({
  id,
  from: 'copilot',
  contents: [],
  stopped: true
});

describe('recordStoppedTurn', () => {
  it('marks the turn that was streaming and keeps what it had streamed', () => {
    const messages = recordStoppedTurn(transcript(), 'r1', placeholder);

    expect(messages).toHaveLength(2);
    expect(messages[1].stopped).toBe(true);
    expect(messages[1].contents).toEqual(['partial answer']);
  });

  it('adds a marker-only message when nothing had streamed yet', () => {
    const messages = recordStoppedTurn(
      [{ id: 'u1', from: 'user', contents: ['hello'] }],
      'r2',
      placeholder
    );

    expect(messages).toHaveLength(2);
    expect(messages[1]).toEqual({
      id: 'r2',
      from: 'copilot',
      contents: [],
      stopped: true
    });
  });

  it('does not add a second message for a turn already marked', () => {
    // The two cases the caller must not confuse: an id that is absent gets a
    // new message, an id already marked gets nothing. Conflating them puts
    // two messages in the transcript under one id.
    const already: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: [], stopped: true }
    ];

    const messages = recordStoppedTurn(already, 'r1', placeholder);

    expect(messages).toBe(already);
    expect(messages).toHaveLength(1);
  });

  it('records nothing for an empty id rather than matching an empty message id', () => {
    // '' is the sentinel for "no turn in flight", so it must not match a
    // message that happens to carry an empty id, and must not append either.
    const before: ITestMessage[] = [{ id: '', from: 'copilot', contents: [] }];

    const messages = recordStoppedTurn(before, '', placeholder);

    expect(messages).toBe(before);
    expect(messages[0].stopped).toBeUndefined();
  });

  it('returns the untouched messages by reference so memoized rows are pruned', () => {
    const before = transcript();

    const messages = recordStoppedTurn(before, 'r1', placeholder);

    expect(messages[0]).toBe(before[0]);
    expect(messages[1]).not.toBe(before[1]);
  });

  it('does not mutate the messages it was given', () => {
    const before = transcript();

    recordStoppedTurn(before, 'r1', placeholder);

    expect(before[1].stopped).toBeUndefined();
  });

  it('marks only the turn that was stopped', () => {
    const twoResponses: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['first'] },
      { id: 'r2', from: 'copilot', contents: ['second'] }
    ];

    const messages = recordStoppedTurn(twoResponses, 'r2', placeholder);

    expect(messages.map(m => m.stopped)).toEqual([undefined, true]);
  });
});

describe('restoreStoppedMarkers', () => {
  it('re-marks a message a stale snapshot carried back unmarked', () => {
    // The snapshot promptRequestHandler writes was taken before the stop, so
    // it holds an unmarked copy of the message the user has since stopped.
    const stale: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['partial'] },
      { id: 'r2', from: 'copilot', contents: ['other turn'] }
    ];

    const messages = restoreStoppedMarkers(stale, new Set(['r1']));

    expect(messages.map(m => m.stopped)).toEqual([true, undefined]);
    expect(messages[0].contents).toEqual(['partial']);
  });

  it('leaves the list alone when nothing was stopped', () => {
    const before = transcript();

    expect(restoreStoppedMarkers(before, new Set())).toBe(before);
  });

  it('returns the same list when every stopped id is already marked', () => {
    const before: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: [], stopped: true }
    ];

    expect(restoreStoppedMarkers(before, new Set(['r1']))).toBe(before);
  });

  it('ignores stopped ids that are not in the list', () => {
    const before = transcript();

    expect(restoreStoppedMarkers(before, new Set(['gone']))).toBe(before);
  });

  it('does not mutate the messages it was given', () => {
    const before = transcript();

    restoreStoppedMarkers(before, new Set(['r1']));

    expect(before[1].stopped).toBeUndefined();
  });
});
