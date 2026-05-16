// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { ReactNode } from 'react';

/**
 * Modal-form shell shared by the simple add/install dialogs across the
 * Claude-MCP and Plugins panels. Owns:
 *   * backdrop + card layout (cancel-on-backdrop-click)
 *   * title / body / actions slots
 *   * Escape-to-cancel keyboard handler
 *   * inline error rendering when ``error`` is non-null
 *   * primary button label transitions for the submitting state
 *
 * Multi-step flows (e.g. the GitHub-import preview-then-install dialog in
 * ``skills-panel.tsx``) keep their own shell since fitting them here
 * would bloat the abstraction.
 */
export function FormDialog(props: {
  title: string;
  submitLabel: string;
  submitInProgressLabel?: string;
  canSubmit: boolean;
  submitting: boolean;
  error?: string | null;
  onCancel: () => void;
  onSubmit: () => void;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className="nbi-modal-backdrop" onClick={props.onCancel}>
      <div
        className="nbi-modal-card"
        role="dialog"
        aria-modal="true"
        onClick={e => e.stopPropagation()}
        onKeyDown={e => {
          if (e.key === 'Escape' && !props.submitting) {
            props.onCancel();
          }
        }}
        tabIndex={-1}
      >
        <div className="nbi-modal-title">{props.title}</div>
        <div className="nbi-modal-body">
          {props.children}
          {props.error && (
            <div className="nbi-skills-error" role="alert">
              {props.error}
            </div>
          )}
        </div>
        <div className="nbi-modal-actions">
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={props.onCancel}
            disabled={props.submitting}
          >
            <div className="jp-Dialog-buttonLabel">Cancel</div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            onClick={props.onSubmit}
            disabled={!props.canSubmit}
          >
            <div className="jp-Dialog-buttonLabel">
              {props.submitting
                ? (props.submitInProgressLabel ?? `${props.submitLabel}…`)
                : props.submitLabel}
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}
