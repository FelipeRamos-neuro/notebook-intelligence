import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { ToolCallCard } from '../../src/components/tool-call-card';

const editToolCall = {
  id: 'e1',
  title: 'Editing file',
  kind: 'edit',
  status: 'completed',
  diffs: [
    {
      path: 'src/a.py',
      lines: [
        { type: 'remove', content: 'x = 1' },
        { type: 'add', content: 'x = 2' }
      ],
      truncated: false
    }
  ]
};

describe('ToolCallCard', () => {
  it('renders the title and an in-progress status', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't1',
          title: 'Reading file',
          kind: 'read',
          status: 'in_progress'
        }}
      />
    );
    expect(screen.getByText('Reading file')).toBeInTheDocument();
    expect(container.querySelector('.nbi-tool-call')).toHaveClass(
      'nbi-tool-call-in-progress'
    );
    // Status reaches screen readers as text (icons are decorative).
    expect(screen.getByText('in progress')).toBeInTheDocument();
  });

  it('renders a completed status', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't2',
          title: 'Editing cell',
          kind: 'edit',
          status: 'completed'
        }}
      />
    );
    expect(container.querySelector('.nbi-tool-call')).toHaveClass(
      'nbi-tool-call-completed'
    );
    expect(screen.getByText('completed')).toBeInTheDocument();
  });

  it('renders a failed status', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't3',
          title: 'Running shell command',
          kind: 'execute',
          status: 'failed'
        }}
      />
    );
    expect(container.querySelector('.nbi-tool-call')).toHaveClass(
      'nbi-tool-call-failed'
    );
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('falls back gracefully for an unknown kind and status', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't4',
          title: 'Mystery',
          kind: 'weird',
          status: 'queued'
        }}
      />
    );
    expect(screen.getByText('Mystery')).toBeInTheDocument();
    // Unknown status is kept verbatim (kebabbed) as the modifier + sr label.
    expect(container.querySelector('.nbi-tool-call')).toHaveClass(
      'nbi-tool-call-queued'
    );
    expect(screen.getByText('queued')).toBeInTheDocument();
  });

  it('renders a cancelled status', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't6',
          title: 'Running shell command',
          kind: 'execute',
          status: 'cancelled'
        }}
      />
    );
    expect(container.querySelector('.nbi-tool-call')).toHaveClass(
      'nbi-tool-call-cancelled'
    );
    expect(screen.getByText('cancelled')).toBeInTheDocument();
  });

  it('renders a leading kind icon and a status icon, both decorative', () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          id: 't5',
          title: 'Reading',
          kind: 'read',
          status: 'completed'
        }}
      />
    );
    const kindIcon = container.querySelector('.nbi-tool-call-kind-icon');
    const statusIcon = container.querySelector('.nbi-tool-call-status-icon');
    expect(kindIcon).toBeTruthy();
    expect(statusIcon).toBeTruthy();
    // Icons are decorative; status reaches screen readers via the sr-only text.
    expect(kindIcon).toHaveAttribute('aria-hidden', 'true');
    expect(statusIcon).toHaveAttribute('aria-hidden', 'true');
  });

  it('renders an inline diff with add/remove lines and the path', () => {
    const { container } = render(<ToolCallCard toolCall={editToolCall} />);
    expect(screen.getByText('src/a.py')).toBeInTheDocument();
    expect(container.querySelector('.nbi-diff-remove')).toHaveTextContent(
      'x = 1'
    );
    expect(container.querySelector('.nbi-diff-add')).toHaveTextContent('x = 2');
  });

  it('collapses and expands the diff via the toggle', () => {
    const { container } = render(<ToolCallCard toolCall={editToolCall} />);
    // Default expanded.
    expect(container.querySelector('.nbi-tool-call-diffs')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide diff' }));
    expect(
      container.querySelector('.nbi-tool-call-diffs')
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show diff' }));
    expect(container.querySelector('.nbi-tool-call-diffs')).toBeInTheDocument();
  });

  it('shows a truncation marker when the diff was truncated', () => {
    render(
      <ToolCallCard
        toolCall={{
          ...editToolCall,
          diffs: [{ ...editToolCall.diffs[0], truncated: true }]
        }}
      />
    );
    expect(screen.getByText('diff truncated')).toBeInTheDocument();
  });

  it('shows no diff toggle when there are no diffs', () => {
    render(
      <ToolCallCard
        toolCall={{
          id: 'r',
          title: 'Reading file',
          kind: 'read',
          status: 'completed'
        }}
      />
    );
    expect(
      screen.queryByRole('button', { name: /diff/ })
    ).not.toBeInTheDocument();
  });
});
