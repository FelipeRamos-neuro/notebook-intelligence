// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Small helpers for the chat sidebar's "Generating" progress indicator.
// Pulled out so each piece (elapsed-time formatting, heartbeat staleness)
// can be unit-tested without dragging JupyterLab into Jest's import graph.

// 30 seconds without a heartbeat counts as "stalled" — long enough that a
// healthy round-trip + retransmission delay can't account for it, short
// enough that a real hang is signaled while there's still time to act.
// The Claude heartbeat fires every 20s server-side, so 30s gives roughly
// 1.5 expected intervals of slack before we change copy.
export const HEARTBEAT_STALE_MS = 30_000;

export function formatElapsedSeconds(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  const ss = String(seconds).padStart(2, '0');
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${ss}`;
  }
  return `${minutes}:${ss}`;
}

export function isHeartbeatStale(
  lastHeartbeatAt: number | null,
  now: number,
  staleMs: number = HEARTBEAT_STALE_MS
): boolean {
  if (lastHeartbeatAt === null) {
    return false;
  }
  return now - lastHeartbeatAt > staleMs;
}
