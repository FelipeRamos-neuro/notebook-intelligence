// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// A streaming turn used to rewrite the transcript from a snapshot taken when
// it started, so anything that changed on an earlier message in the meantime
// was reverted by the turn's next delta.
import { upsertMessageById } from '../../src/chat-transcript';

interface ITestMessage {
  id: string;
  from: string;
  contents: string[];
  feedback?: string;
}

describe('upsertMessageById', () => {
  it('appends a message the transcript does not have yet', () => {
    const before: ITestMessage[] = [
      { id: 'u1', from: 'user', contents: ['hello'] }
    ];

    const messages = upsertMessageById(before, {
      id: 'r1',
      from: 'copilot',
      contents: ['answering']
    });

    expect(messages.map(m => m.id)).toEqual(['u1', 'r1']);
  });

  it('replaces the streaming turn in place rather than moving it to the end', () => {
    const before: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['first'] },
      { id: 'u2', from: 'user', contents: ['second question'] }
    ];

    const messages = upsertMessageById(before, {
      id: 'r1',
      from: 'copilot',
      contents: ['first, extended']
    });

    expect(messages.map(m => m.id)).toEqual(['r1', 'u2']);
    expect(messages[0].contents).toEqual(['first, extended']);
  });

  it('leaves changes made to other messages alone', () => {
    // This is the regression: the old write rebuilt the list from a snapshot,
    // so a flag set on an earlier message after that snapshot was taken (a
    // feedback rating, a stopped marker) was reverted by the next delta.
    const before: ITestMessage[] = [
      {
        id: 'r1',
        from: 'copilot',
        contents: ['earlier'],
        feedback: 'positive'
      },
      { id: 'r2', from: 'copilot', contents: ['streaming'] }
    ];

    const messages = upsertMessageById(before, {
      id: 'r2',
      from: 'copilot',
      contents: ['streaming more']
    });

    expect(messages[0].feedback).toBe('positive');
    expect(messages[0]).toBe(before[0]);
  });

  it('does not mutate the transcript it was given', () => {
    const before: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['one'] }
    ];

    upsertMessageById(before, {
      id: 'r1',
      from: 'copilot',
      contents: ['two']
    });

    expect(before[0].contents).toEqual(['one']);
    expect(before).toHaveLength(1);
  });

  it('always returns a fresh array', () => {
    const before: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['one'] }
    ];

    const appended = upsertMessageById(before, {
      id: 'r2',
      from: 'copilot',
      contents: ['two']
    });
    const replaced = upsertMessageById(before, {
      id: 'r1',
      from: 'copilot',
      contents: ['one, extended']
    });

    expect(appended).not.toBe(before);
    expect(replaced).not.toBe(before);
  });
});

describe('upsertMessageById ordering', () => {
  it('puts a new response next to the prompt it answers', () => {
    // Two turns overlapping: B's prompt is already in the transcript when A's
    // first delta lands. Appending would file A's answer under B's question.
    const before: ITestMessage[] = [
      { id: 'userA', from: 'user', contents: ['question A'] },
      { id: 'userB', from: 'user', contents: ['question B'] }
    ];

    const messages = upsertMessageById(
      before,
      { id: 'responseA', from: 'copilot', contents: ['answer A'] },
      'userA'
    );

    expect(messages.map(m => m.id)).toEqual(['userA', 'responseA', 'userB']);
  });

  it('keeps both turns paired through an interleaved sequence', () => {
    let messages: ITestMessage[] = [];
    messages = upsertMessageById(messages, {
      id: 'userA',
      from: 'user',
      contents: ['A']
    });
    messages = upsertMessageById(messages, {
      id: 'userB',
      from: 'user',
      contents: ['B']
    });
    messages = upsertMessageById(
      messages,
      { id: 'responseA', from: 'copilot', contents: ['a1'] },
      'userA'
    );
    messages = upsertMessageById(
      messages,
      { id: 'responseB', from: 'copilot', contents: ['b1'] },
      'userB'
    );
    messages = upsertMessageById(
      messages,
      { id: 'responseA', from: 'copilot', contents: ['a1 a2'] },
      'userA'
    );

    expect(messages.map(m => m.id)).toEqual([
      'userA',
      'responseA',
      'userB',
      'responseB'
    ]);
    expect(messages[1].contents).toEqual(['a1 a2']);
  });

  it('appends when the prompt it answers is gone', () => {
    const before: ITestMessage[] = [
      { id: 'userB', from: 'user', contents: ['B'] }
    ];

    const messages = upsertMessageById(
      before,
      { id: 'responseA', from: 'copilot', contents: ['a'] },
      'userA'
    );

    expect(messages.map(m => m.id)).toEqual(['userB', 'responseA']);
  });

  it('keeps a rating the streaming turn does not know about', () => {
    // The turn rebuilds only the fields it owns, so a replace that dropped the
    // rest would un-rate a message the user just rated.
    const before: ITestMessage[] = [
      { id: 'r1', from: 'copilot', contents: ['partial'], feedback: 'positive' }
    ];

    const messages = upsertMessageById(before, {
      id: 'r1',
      from: 'copilot',
      contents: ['partial, extended']
    });

    expect(messages[0].feedback).toBe('positive');
    expect(messages[0].contents).toEqual(['partial, extended']);
  });
});
