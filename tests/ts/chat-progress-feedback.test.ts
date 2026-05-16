// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  HEARTBEAT_STALE_MS,
  formatElapsedSeconds,
  isHeartbeatStale
} from '../../src/chat-progress-feedback';

describe('formatElapsedSeconds', () => {
  it('renders zero as 0:00', () => {
    expect(formatElapsedSeconds(0)).toBe('0:00');
  });

  it('pads single-digit seconds with a leading zero', () => {
    expect(formatElapsedSeconds(5)).toBe('0:05');
    expect(formatElapsedSeconds(9)).toBe('0:09');
  });

  it('rolls over to the next minute correctly', () => {
    expect(formatElapsedSeconds(59)).toBe('0:59');
    expect(formatElapsedSeconds(60)).toBe('1:00');
    expect(formatElapsedSeconds(125)).toBe('2:05');
  });

  it('drops to h:mm:ss above one hour', () => {
    // Agentic runs that hit a one-hour timeout are vanishingly rare but
    // the format shouldn't break catastrophically when they happen.
    expect(formatElapsedSeconds(3599)).toBe('59:59');
    expect(formatElapsedSeconds(3600)).toBe('1:00:00');
    expect(formatElapsedSeconds(3725)).toBe('1:02:05');
  });

  it('floors fractional seconds rather than rounding', () => {
    // Math.floor: 23.9 → 23 not 24. Avoids "0:24" appearing for half a
    // second before the next tick catches up.
    expect(formatElapsedSeconds(23.9)).toBe('0:23');
  });

  it('treats negative input as zero', () => {
    // Defensive: a clock skew that produces a negative delta should
    // render the same as "just started" rather than a minus sign.
    expect(formatElapsedSeconds(-5)).toBe('0:00');
  });
});

describe('isHeartbeatStale', () => {
  const NOW = 1_000_000;

  it('returns false when no heartbeat has arrived yet', () => {
    // Pre-first-heartbeat we have nothing to compare against; treating
    // null as not-stale avoids a spurious "may be slow" banner during
    // the warmup window of every request.
    expect(isHeartbeatStale(null, NOW)).toBe(false);
  });

  it('returns false when the last heartbeat is recent', () => {
    expect(isHeartbeatStale(NOW - 1_000, NOW)).toBe(false);
    expect(isHeartbeatStale(NOW - 25_000, NOW)).toBe(false);
  });

  it('treats exactly the threshold as still fresh (strict greater-than)', () => {
    // The 30s threshold is the boundary; a heartbeat that just barely
    // arrived inside it should NOT flip the indicator.
    expect(isHeartbeatStale(NOW - HEARTBEAT_STALE_MS, NOW)).toBe(false);
  });

  it('returns true once the gap exceeds the threshold', () => {
    expect(isHeartbeatStale(NOW - HEARTBEAT_STALE_MS - 1, NOW)).toBe(true);
    expect(isHeartbeatStale(NOW - 60_000, NOW)).toBe(true);
  });

  it('honors a custom threshold for testing harnesses', () => {
    expect(isHeartbeatStale(NOW - 5_000, NOW, 1_000)).toBe(true);
    expect(isHeartbeatStale(NOW - 500, NOW, 1_000)).toBe(false);
  });
});
