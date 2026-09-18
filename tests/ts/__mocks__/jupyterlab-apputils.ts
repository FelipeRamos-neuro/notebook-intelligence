// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stub for @jupyterlab/apputils. The real package is ESM and pulls in
// @jupyterlab/ui-components, which jest's CommonJS pipeline can't load.
// Mock only the surface the modules under test touch: Notification for
// terminal-drag.ts, and ReactWidget / Dialog so settings-panel.tsx can be
// imported for its pure helpers and presentational components.

export const Notification = {
  error: jest.fn(),
  warning: jest.fn(),
  info: jest.fn(),
  success: jest.fn()
};

export class ReactWidget {}

export class Dialog {}

export const showDialog = jest.fn();
