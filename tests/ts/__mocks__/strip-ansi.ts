// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// strip-ansi ships ESM-only, which jest's CJS test runner can't load
// directly. The real package is a single regex-based string replace, so
// this mock reimplements that regex rather than reworking the jest
// transform pipeline for one dependency.
export default function stripAnsi(str: string): string {
  return str.replace(
    // eslint-disable-next-line no-control-regex
    /[][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g,
    ''
  );
}
