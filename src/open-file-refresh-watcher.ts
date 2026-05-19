// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import type { DocumentRegistry, DocumentWidget } from '@jupyterlab/docregistry';
import type { Contents } from '@jupyterlab/services';

export const DEFAULT_REFRESH_POLL_INTERVAL_MS = 3000;

/**
 * Inputs the revert decision depends on. Keeping this pure (no
 * JupyterLab types) lets the unit test pin the policy without
 * instantiating a real Context.
 */
export interface IRevertDecisionInputs {
  diskLastModified: string | null | undefined;
  contextLastModified: string | null | undefined;
  isDirty: boolean;
  isReady: boolean;
  isDisposed: boolean;
}

/**
 * Whether the open document's in-memory model should be reverted to
 * match the on-disk version. The rules, in order:
 *
 *   1. Skip if the context is gone (disposed) or not yet populated
 *      (`isReady` false). Calling `revert()` against an unready
 *      context races the initial load.
 *   2. Skip if the user has unsaved local edits (`isDirty`). Silently
 *      clobbering their work would be hostile; the standard
 *      JupyterLab "newer on disk" prompt will surface on save.
 *   3. Skip if we can't compare timestamps (either side missing).
 *   4. Revert iff disk's `last_modified` is strictly greater than
 *      the context's last-known value. Equal timestamps mean the in-
 *      memory copy is already current (a save we initiated, or a
 *      no-op re-read).
 *
 * Last-modified values arrive as ISO-8601 strings from the Contents
 * API; lexicographic comparison is correct for that grammar.
 */
export function shouldRevertContext({
  diskLastModified,
  contextLastModified,
  isDirty,
  isReady,
  isDisposed
}: IRevertDecisionInputs): boolean {
  if (isDisposed || !isReady) {
    return false;
  }
  if (isDirty) {
    return false;
  }
  if (!diskLastModified || !contextLastModified) {
    return false;
  }
  return diskLastModified > contextLastModified;
}

/**
 * Side-effect surface the watcher reaches into. Extracted so tests
 * can pass a thin fake without standing up a real JupyterFrontEnd or
 * Contents singleton.
 */
export interface IRefreshWatcherEnv {
  /** Yield every currently-open DocumentWidget in the main shell. */
  iterDocumentWidgets: () => Iterable<DocumentWidget>;
  /** Fetch on-disk metadata without the body (cheap stat-shaped call). */
  fetchDiskModel: (path: string) => Promise<Contents.IModel>;
  /** Set/clear the polling interval. Pulled out for fake timers in tests. */
  setInterval: (handler: () => void, ms: number) => unknown;
  clearInterval: (handle: unknown) => void;
}

export interface IRefreshWatcherOptions {
  env: IRefreshWatcherEnv;
  intervalMs?: number;
  /** Re-checked on every tick so a settings toggle takes effect without restart. */
  isEnabled: () => boolean;
  /** Hook for tests / telemetry — fired once per revert (the heavy outcome). */
  onRevert?: (path: string) => void;
  /** Hook for tests / diagnostics — fired when a check throws. */
  onError?: (path: string, error: unknown) => void;
}

/**
 * Polls every open document widget on a fixed cadence, comparing the
 * file's on-disk `last_modified` against the context's last-known
 * value, and calls `context.revert()` when the disk is newer.
 *
 * Why polling at all (when the Contents API exposes a `fileChanged`
 * signal): agents like Claude write directly to the filesystem,
 * bypassing the API. The signal fires for Lab-routed writes only.
 * Polling catches both paths uniformly and keeps the watcher
 * single-purpose.
 *
 * Returns a teardown function the caller invokes on plugin
 * deactivation to stop the timer.
 */
export function attachOpenFileRefreshWatcher(
  options: IRefreshWatcherOptions
): () => void {
  const intervalMs = options.intervalMs ?? DEFAULT_REFRESH_POLL_INTERVAL_MS;

  let inFlight = false;
  const tick = async (): Promise<void> => {
    if (!options.isEnabled()) {
      return;
    }
    // Re-entrancy guard: a slow Contents.get on one widget shouldn't
    // pile up additional ticks while it resolves. Skip rather than
    // queue so a transient server slowdown doesn't snowball.
    if (inFlight) {
      return;
    }
    inFlight = true;
    try {
      const seen = new Set<string>();
      for (const widget of options.env.iterDocumentWidgets()) {
        const context: DocumentRegistry.Context | undefined = widget.context;
        if (!context || !context.path) {
          continue;
        }
        // Dedupe across widgets sharing the same context (split-view,
        // notebook + editor view, etc.); reverting once per path is
        // enough since the context is the shared mutable state.
        if (seen.has(context.path)) {
          continue;
        }
        seen.add(context.path);
        await checkOneContext(context, options);
      }
    } finally {
      inFlight = false;
    }
  };

  const handle = options.env.setInterval(() => {
    // Swallow tick-level errors so a single bad path can't kill the
    // poller; per-context errors are reported via onError above.
    void tick();
  }, intervalMs);

  return () => {
    options.env.clearInterval(handle);
  };
}

async function checkOneContext(
  context: DocumentRegistry.Context,
  options: IRefreshWatcherOptions
): Promise<void> {
  try {
    const diskModel = await options.env.fetchDiskModel(context.path);
    const decision = shouldRevertContext({
      diskLastModified: diskModel.last_modified,
      contextLastModified: context.contentsModel?.last_modified,
      isDirty: context.model.dirty,
      isReady: context.isReady,
      isDisposed: context.isDisposed
    });
    if (!decision) {
      return;
    }
    await context.revert();
    options.onRevert?.(context.path);
  } catch (error) {
    options.onError?.(context.path, error);
  }
}
