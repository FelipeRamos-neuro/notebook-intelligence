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

// What the chat mode select sits on, so it looks as it did when transparent.
const FOOTER_BACKGROUND = /var\(\s*--jp-cell-editor-background/;

describe('select colors follow the JupyterLab theme', () => {
  it('gives the chat mode select the footer background, not transparent', () => {
    const body = declarations('.chat-mode-select');
    expect(body).toMatch(
      new RegExp(`background-color:\\s*${FOOTER_BACKGROUND.source}`)
    );
    expect(body).not.toMatch(/background-color:\s*initial/);
  });

  it('matches the chat mode popup to the select it belongs to, with fallbacks', () => {
    const body = declarations('.chat-mode-select option');
    expect(body).toMatch(
      /background-color:\s*var\(\s*--jp-cell-editor-background,\s*var\(\s*--jp-layout-color1,\s*Canvas\s*\)\s*\)/
    );
    expect(body).toMatch(
      /\bcolor:\s*var\(\s*--jp-ui-font-color0,\s*CanvasText\s*\)/
    );
  });

  it.each([
    '.nbi-form-field select option',
    '.config-dialog-body select option',
    '.nbi-perf-control select option'
  ])('themes the option list for %s, with fallbacks', selector => {
    const body = declarations(selector);
    // A theme that omits --jp-layout-color2 must not leave the option
    // transparent, which is the original bug.
    expect(body).toMatch(
      /background-color:\s*var\(\s*--jp-layout-color2,\s*var\(\s*--jp-layout-color1,\s*Canvas\s*\)\s*\)/
    );
    expect(body).toMatch(
      /\bcolor:\s*var\(\s*--jp-ui-font-color0,\s*CanvasText\s*\)/
    );
  });
});
