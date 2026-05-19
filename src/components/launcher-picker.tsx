// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, {
  ChangeEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState
} from 'react';
import { IClaudeSessionInfo, NBIAPI } from '../api';

export interface ILauncherPickerProps {
  onSessionSelected: (session: IClaudeSessionInfo) => void;
}

// Pick a short, glanceable label for a session's project. The basename
// of the cwd is usually the project's actual name; the full path stays
// available via the row's `title` attribute on hover.
function projectLabel(cwd: string | undefined | null): string {
  if (!cwd) {
    return '';
  }
  const trimmed = cwd.replace(/\/+$/, '');
  const idx = trimmed.lastIndexOf('/');
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

export function LauncherPicker({
  onSessionSelected
}: ILauncherPickerProps): JSX.Element {
  const [sessions, setSessions] = useState<IClaudeSessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    NBIAPI.listClaudeSessions('all')
      .then(result => {
        setSessions(result.sessions);
        setLoading(false);
      })
      .catch((reason: any) => {
        setError(String(reason?.message ?? reason ?? 'Unknown error'));
        setLoading(false);
      });
  }, []);

  const needle = filter.toLowerCase();
  const filtered = filter
    ? sessions.filter(
        s =>
          s.preview?.toLowerCase().includes(needle) ||
          s.cwd?.toLowerCase().includes(needle)
      )
    : sessions;

  // A held-over index against a refetched session set could silently
  // point at a different session, so reset on any sessions change —
  // not just length, which would miss equal-length-but-different sets.
  useEffect(() => {
    setHighlightedIndex(-1);
  }, [filter, sessions]);

  useEffect(() => {
    if (highlightedIndex < 0 || !listRef.current) {
      return;
    }
    const row = listRef.current.children[highlightedIndex] as
      | HTMLElement
      | undefined;
    row?.scrollIntoView({ block: 'nearest' });
  }, [highlightedIndex]);

  // Refs so the document-level keydown listener installed below can read
  // the latest values without re-attaching on every render.
  const filteredRef = useRef(filtered);
  const highlightedIndexRef = useRef(highlightedIndex);
  const onSessionSelectedRef = useRef(onSessionSelected);
  filteredRef.current = filtered;
  highlightedIndexRef.current = highlightedIndex;
  onSessionSelectedRef.current = onSessionSelected;

  // Lumino's Dialog catches Enter at capture phase on the dialog node and
  // triggers its default OK button (the "New Session" button), so a React
  // bubble-phase handler never sees Enter inside the picker. Attach a
  // document-capture listener here so we beat the dialog and activate the
  // highlighted row instead — falling through to the dialog's New Session
  // path only when there's nothing selectable to land on.
  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Enter') {
        return;
      }
      const node = containerRef.current;
      if (!node || !node.contains(e.target as Node)) {
        return;
      }
      const list = filteredRef.current;
      if (list.length === 0) {
        return;
      }
      e.preventDefault();
      e.stopImmediatePropagation();
      const idx = highlightedIndexRef.current;
      onSessionSelectedRef.current(list[idx >= 0 ? idx : 0]);
    };
    document.addEventListener('keydown', handler, { capture: true });
    return () =>
      document.removeEventListener('keydown', handler, { capture: true });
  }, []);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (filtered.length === 0) {
      return;
    }
    // From "no row highlighted" (-1), ArrowDown jumps to the first row
    // and ArrowUp jumps to the last — each direction lands at its
    // nearest end so the user always reaches a valid row in one press.
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightedIndex(i => (i < 0 || i >= filtered.length - 1 ? 0 : i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightedIndex(i => (i <= 0 ? filtered.length - 1 : i - 1));
    }
  };

  if (loading) {
    return (
      <div className="nbi-claude-code-picker-status">
        Loading sessions&#8230;
      </div>
    );
  }
  if (error) {
    return (
      <div className="nbi-claude-code-picker-status nbi-claude-code-picker-error">
        {error}
      </div>
    );
  }
  const activeRowId =
    highlightedIndex >= 0
      ? `nbi-claude-session-row-${filtered[highlightedIndex].session_id}`
      : undefined;
  return (
    <div
      className="nbi-claude-code-picker-body"
      ref={containerRef}
      onKeyDown={handleKeyDown}
    >
      <input
        className="nbi-claude-code-picker-search"
        type="text"
        placeholder="Filter sessions..."
        value={filter}
        onChange={(e: ChangeEvent<HTMLInputElement>) =>
          setFilter(e.target.value)
        }
        autoFocus
        role="combobox"
        aria-expanded={filtered.length > 0}
        aria-controls="nbi-claude-session-listbox"
        aria-activedescendant={activeRowId}
      />
      <div
        className="nbi-claude-code-picker-list"
        id="nbi-claude-session-listbox"
        role="listbox"
        ref={listRef}
      >
        {filtered.length === 0 ? (
          <div className="nbi-claude-code-picker-empty">
            {filter
              ? 'No sessions match your filter.'
              : 'No previous sessions found.'}
          </div>
        ) : (
          filtered.map((session, index) => {
            const isHighlighted = index === highlightedIndex;
            return (
              <div
                key={session.session_id}
                id={`nbi-claude-session-row-${session.session_id}`}
                role="option"
                className={
                  'nbi-claude-code-picker-session' +
                  (isHighlighted ? ' highlighted' : '')
                }
                tabIndex={0}
                aria-selected={isHighlighted}
                onClick={() => onSessionSelected(session)}
                onFocus={() => setHighlightedIndex(index)}
              >
                <div className="nbi-claude-code-picker-session-top">
                  <span className="nbi-claude-code-picker-session-id">
                    {session.session_id.slice(0, 8)}
                  </span>
                  {/* Render the basename of the project as the inline
                      label; keep the full path on hover via title so a
                      user with several similarly-named projects can
                      disambiguate without losing the path entirely.
                      Screen readers don't expose `title` on <span>
                      reliably, so duplicate the full path into
                      aria-label whenever it differs from the visible
                      basename. */}
                  {(() => {
                    const full = session.cwd ?? '';
                    const label = projectLabel(full);
                    const fullDiffersFromLabel = full && full !== label;
                    return (
                      <span
                        className="nbi-claude-code-picker-session-project"
                        title={full}
                        aria-label={fullDiffersFromLabel ? full : undefined}
                      >
                        {label}
                      </span>
                    );
                  })()}
                </div>
                {session.preview && (
                  <div className="nbi-claude-code-picker-msg">
                    {session.preview}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
