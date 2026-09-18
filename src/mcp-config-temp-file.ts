// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { Contents } from '@jupyterlab/services';

/**
 * Give the newly written config file the editor's fixed temp name.
 *
 * Usually nothing holds that name, so the rename just takes it. What can hold
 * it is a file from a session that ended without closing the editor, or one
 * the user put there themselves, and the rename is what reports that. Sending
 * a DELETE first instead, to be sure, answers 404 on every open because there
 * is normally nothing to delete.
 */
export async function claimMCPTempFileName(
  contents: Pick<Contents.IManager, 'rename' | 'delete'>,
  fromPath: string,
  tempPath: string
): Promise<void> {
  try {
    await contents.rename(fromPath, tempPath);
  } catch (error) {
    try {
      await contents.delete(tempPath);
    } catch (deleteError) {
      // The rename's error says why the name was unavailable; the delete's
      // only says the cleanup did not work, so keep the first one.
      throw error;
    }
    await contents.rename(fromPath, tempPath);
  }
}
