// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Opening the MCP config editor used to DELETE its temp file first, which
// answered 404 on every open because the file is normally not there.
import { claimMCPTempFileName } from '../../src/mcp-config-temp-file';

function fakeContents(renameFails: number) {
  const calls: string[] = [];
  let failures = renameFails;
  return {
    calls,
    rename: jest.fn(async (from: string, to: string) => {
      calls.push(`rename:${from}->${to}`);
      if (failures > 0) {
        failures -= 1;
        throw new Error('File already exists');
      }
      return { path: to } as never;
    }),
    delete: jest.fn(async (path: string) => {
      calls.push(`delete:${path}`);
    })
  };
}

describe('claimMCPTempFileName', () => {
  it('does not touch the temp name when it is free', async () => {
    const contents = fakeContents(0);

    await claimMCPTempFileName(contents, 'untitled.json', 'temp.json');

    expect(contents.calls).toEqual(['rename:untitled.json->temp.json']);
    expect(contents.delete).not.toHaveBeenCalled();
  });

  it('clears a file left behind by an earlier session, then takes the name', async () => {
    const contents = fakeContents(1);

    await claimMCPTempFileName(contents, 'untitled.json', 'temp.json');

    expect(contents.calls).toEqual([
      'rename:untitled.json->temp.json',
      'delete:temp.json',
      'rename:untitled.json->temp.json'
    ]);
  });

  it('reports why the name was unavailable when the cleanup fails', async () => {
    const contents = fakeContents(1);
    contents.delete = jest.fn(async () => {
      throw new Error('Directory not empty');
    });

    await expect(
      claimMCPTempFileName(contents, 'untitled.json', 'temp.json')
    ).rejects.toThrow('File already exists');
  });

  it('reports a rename that still fails after the cleanup', async () => {
    const contents = fakeContents(2);

    await expect(
      claimMCPTempFileName(contents, 'untitled.json', 'temp.json')
    ).rejects.toThrow('File already exists');
  });
});
