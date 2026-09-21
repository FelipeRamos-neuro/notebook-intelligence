// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// The chat mode dropdown reset its background to `initial` while keeping a
// theme-derived text color, so in a dark JupyterLab theme the native option
// list rendered light text on a light popup. jsdom cannot render a native
// popup, so pin the stylesheet declarations that prevent it.
import * as fs from 'fs';
import * as path from 'path';

const css = fs
  .readFileSync(path.join(__dirname, '..', '..', 'style', 'base.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

function declarations(selector: string): string {
  const rules = css
    .split('}')
    .map(chunk => chunk.split('{'))
    .filter(
      ([sel, body]) =>
        body !== undefined &&
        sel
          .split(',')
          .map(part => part.trim())
          .includes(selector)
    );
  return rules.map(([, body]) => body).join('\n');
}

describe('select colors follow the JupyterLab theme', () => {
  it('gives the chat mode select a themed background', () => {
    const body = declarations('.chat-mode-select');
    expect(body).toMatch(/background-color:\s*var\(--jp-/);
    expect(body).not.toMatch(/background-color:\s*initial/);
  });

  it.each([
    '.chat-mode-select option',
    '.nbi-form-field select option',
    '.config-dialog-body select option',
    '.nbi-perf-control select option'
  ])('themes the option list for %s', selector => {
    const body = declarations(selector);
    expect(body).toMatch(/background-color:\s*var\(--jp-/);
    expect(body).toMatch(/\bcolor:\s*var\(--jp-/);
  });
});
