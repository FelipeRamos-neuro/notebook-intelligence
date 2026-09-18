// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stop used to cancel a single remembered message id, which the sidebar input
// was the only path to set, so a turn started by an editor command kept
// running after the user pressed Stop.
import {
  cancelInFlightTurns,
  registerInFlightTurn
} from '../../src/chat-cancel';

describe('cancelInFlightTurns', () => {
  it('cancels every turn still running, not just one', () => {
    const ids = new Set(['sidebar-turn', 'explain-code-turn']);
    const sent: string[] = [];

    const cancelled = cancelInFlightTurns(ids, id => sent.push(id));

    expect(sent.sort()).toEqual(['explain-code-turn', 'sidebar-turn']);
    expect(cancelled.sort()).toEqual(['explain-code-turn', 'sidebar-turn']);
  });

  it('empties the set so a second press cancels nothing again', () => {
    const ids = new Set(['turn']);
    const sent: string[] = [];

    cancelInFlightTurns(ids, id => sent.push(id));
    const second = cancelInFlightTurns(ids, id => sent.push(id));

    expect(ids.size).toBe(0);
    expect(second).toEqual([]);
    expect(sent).toEqual(['turn']);
  });

  it('sends nothing when no turn is running', () => {
    const sent: string[] = [];

    expect(cancelInFlightTurns(new Set(), id => sent.push(id))).toEqual([]);
    expect(sent).toEqual([]);
  });

  it('clears the set before sending, so a failed send cannot re-cancel later', () => {
    // Nothing at today's call sites throws synchronously (the send is async),
    // so this pins the ordering rather than a reachable failure: ids must not
    // survive a send that goes wrong.
    const ids = new Set(['turn-a']);

    expect(() =>
      cancelInFlightTurns(ids, () => {
        throw new Error('socket closed');
      })
    ).toThrow('socket closed');
    expect(ids.size).toBe(0);
  });

  it('cancels whatever the set holds, in one pass', () => {
    const ids = new Set(['a', 'b', 'c']);
    const sent: string[] = [];

    const cancelled = cancelInFlightTurns(ids, id => sent.push(id));

    expect(sent).toHaveLength(3);
    expect(cancelled).toHaveLength(3);
    expect(ids.size).toBe(0);
  });
});

describe('registerInFlightTurn', () => {
  it('records a visible turn so Stop can cancel it', () => {
    const ids = new Set<string>();

    expect(registerInFlightTurn(ids, 'turn-1')).toBe(true);
    expect([...ids]).toEqual(['turn-1']);
  });

  it('does not record a hidden turn, which has no Stop button of its own', () => {
    const ids = new Set<string>();

    expect(
      registerInFlightTurn(ids, 'background-turn', { hideInChat: true })
    ).toBe(false);
    expect(ids.size).toBe(0);
  });

  it('does not record the idle sentinel', () => {
    const ids = new Set<string>();

    expect(registerInFlightTurn(ids, '')).toBe(false);
    expect(ids.size).toBe(0);
  });

  it('is idempotent for a turn already recorded', () => {
    const ids = new Set(['turn-1']);

    registerInFlightTurn(ids, 'turn-1');

    expect([...ids]).toEqual(['turn-1']);
  });
});
