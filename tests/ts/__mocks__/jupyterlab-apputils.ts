// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stub for @jupyterlab/apputils. The real package is ESM and pulls in
// @jupyterlab/ui-components, which jest's CommonJS pipeline can't load.
// terminal-drag.ts only touches Notification; mock just that surface.

export const Notification = {
  error: jest.fn(),
  warning: jest.fn(),
  info: jest.fn(),
  success: jest.fn()
};
