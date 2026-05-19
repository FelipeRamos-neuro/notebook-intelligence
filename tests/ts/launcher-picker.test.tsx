// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('../../src/api', () => ({
  NBIAPI: {
    listClaudeSessions: jest.fn()
  }
}));

import { NBIAPI } from '../../src/api';
import { LauncherPicker } from '../../src/components/launcher-picker';

const api = NBIAPI as any;

describe('LauncherPicker', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders the basename of each session.cwd as the project label', async () => {
    api.listClaudeSessions.mockResolvedValue({
      sessions: [
        {
          session_id: 'aaaaaaaa-1111-1111-1111-111111111111',
          path: '/Users/me/.claude/projects/-Users-me-foo/x.jsonl',
          modified_at: 100,
          created_at: 50,
          preview: 'first prompt',
          cwd: '/Users/me/foo'
        },
        {
          session_id: 'bbbbbbbb-2222-2222-2222-222222222222',
          path: '/Users/me/.claude/projects/-Users-me-bar-baz/y.jsonl',
          modified_at: 200,
          created_at: 150,
          preview: 'second prompt',
          cwd: '/Users/me/bar-baz'
        }
      ],
      currentCwd: '/Users/me/foo'
    });

    render(<LauncherPicker onSessionSelected={jest.fn()} />);

    // Wait for the async fetch to land before asserting labels.
    await screen.findByText('first prompt');

    expect(api.listClaudeSessions).toHaveBeenCalledWith('all');
    // Basename labels, not full paths.
    expect(screen.getByText('foo')).toBeInTheDocument();
    expect(screen.getByText('bar-baz')).toBeInTheDocument();
    // Full path is preserved on the project label's title attr so a
    // user with several similarly-named projects can disambiguate.
    expect(screen.getByText('foo').getAttribute('title')).toBe('/Users/me/foo');
  });

  it('shows an empty-state message when no sessions exist', async () => {
    api.listClaudeSessions.mockResolvedValue({
      sessions: [],
      currentCwd: '/Users/me/foo'
    });

    render(<LauncherPicker onSessionSelected={jest.fn()} />);

    await waitFor(() => {
      expect(
        screen.getByText('No previous sessions found.')
      ).toBeInTheDocument();
    });
  });
});
