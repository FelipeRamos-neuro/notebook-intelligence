// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stopping a response used to leave the transcript untouched, so a stopped
// turn was indistinguishable from a model that answered with nothing.
import { recordStoppedTurn } from '../../src/chat-stopped-turn';

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
