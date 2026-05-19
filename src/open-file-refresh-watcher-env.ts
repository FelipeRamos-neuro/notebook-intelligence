// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Live binding for the open-file refresh watcher. Lives in its own
// file so unit tests can exercise the pure logic in
// `open-file-refresh-watcher.ts` without transitively importing
// `@jupyterlab/docregistry`, which ships ESM that ts-jest's default
// transform can't parse.

import { JupyterFrontEnd } from '@jupyterlab/application';
import { DocumentWidget } from '@jupyterlab/docregistry';
import { Contents } from '@jupyterlab/services';

import { IRefreshWatcherEnv } from './open-file-refresh-watcher';

// JL4 hosts editable documents in 'main' (default) and 'down' (the
// split-down editor pane). Walk the sidebars too: users can drag a
// notebook to 'left' or 'right' in JL4, and a non-DocumentWidget
// sitting in a sidebar costs only the instanceof filter check below.
// 'top'/'header'/'menu'/'bottom' are chrome (toolbars, status bar)
// and never host documents.
const WATCHED_SHELL_AREAS = ['main', 'down', 'left', 'right'] as const;

export function buildRefreshWatcherEnv(
  app: JupyterFrontEnd,
  contents: Contents.IManager
): IRefreshWatcherEnv {
  return {
    iterDocumentWidgets: function* () {
      for (const area of WATCHED_SHELL_AREAS) {
        for (const widget of app.shell.widgets(area)) {
          if (widget instanceof DocumentWidget) {
            yield widget;
          }
        }
      }
    },
    fetchDiskModel: path => contents.get(path, { content: false }),
    setInterval: (handler, ms) => window.setInterval(handler, ms),
    clearInterval: handle => window.clearInterval(handle as number)
  };
}
