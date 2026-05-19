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

export function buildRefreshWatcherEnv(
  app: JupyterFrontEnd,
  contents: Contents.IManager
): IRefreshWatcherEnv {
  return {
    iterDocumentWidgets: function* () {
      for (const widget of app.shell.widgets('main')) {
        if (widget instanceof DocumentWidget) {
          yield widget;
        }
      }
    },
    fetchDiskModel: path => contents.get(path, { content: false }),
    setInterval: (handler, ms) => window.setInterval(handler, ms),
    clearInterval: handle => window.clearInterval(handle as number)
  };
}
