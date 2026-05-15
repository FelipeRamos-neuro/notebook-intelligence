// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useState } from 'react';
import { Dialog, showDialog } from '@jupyterlab/apputils';
import {
  ClaudeMCPScope,
  ClaudeMCPTransport,
  IClaudeMCPAddInput,
  IClaudeMCPServer,
  NBIAPI
} from '../api';

const SCOPES: ClaudeMCPScope[] = ['user', 'project', 'local'];
const TRANSPORTS: ClaudeMCPTransport[] = ['stdio', 'sse', 'http'];

const SCOPE_HINT: Record<ClaudeMCPScope, string> = {
  user: 'available in all your projects',
  project: 'shared via the project repo (.mcp.json)',
  local: 'this project, this user only'
};

export function SettingsPanelComponentClaudeMCP(_props: any): JSX.Element {
  const [servers, setServers] = useState<IClaudeMCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRemoval, setPendingRemoval] = useState<IClaudeMCPServer | null>(
    null
  );

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await NBIAPI.listClaudeMCPServers();
      setServers(list);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleRemove = async (srv: IClaudeMCPServer) => {
    const ok = await showDialog({
      title: 'Remove MCP server?',
      body: `"${srv.name}" will be removed from Claude's ${srv.scope}-scope config.`,
      buttons: [Dialog.cancelButton(), Dialog.warnButton({ label: 'Remove' })]
    });
    if (!ok.button.accept) {
      return;
    }
    setPendingRemoval(srv);
    try {
      await NBIAPI.removeClaudeMCPServer(srv.name, srv.scope);
      await refresh();
    } catch (e: any) {
      setError(`Failed to remove: ${e?.message ?? e}`);
    } finally {
      setPendingRemoval(null);
    }
  };

  const handleAddSubmit = async (input: IClaudeMCPAddInput) => {
    // Errors are rendered inside the dialog so they're not hidden behind the
    // modal backdrop; rethrow so the dialog can keep itself open.
    await NBIAPI.addClaudeMCPServer(input);
    setAddOpen(false);
    await refresh();
  };

  const grouped: Record<ClaudeMCPScope, IClaudeMCPServer[]> = {
    user: [],
    project: [],
    local: []
  };
  for (const srv of servers) {
    (grouped[srv.scope as ClaudeMCPScope] ?? grouped.user).push(srv);
  }

  return (
    <div className="config-dialog-body nbi-skills-panel">
      <div className="nbi-skills-header">
        <div className="nbi-skills-title">MCP Servers</div>
        <div className="nbi-skills-header-actions">
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={refresh}
            disabled={loading}
            title="Re-read Claude's MCP config from disk"
          >
            <div className="jp-Dialog-buttonLabel">
              {loading ? 'Refreshing…' : 'Refresh'}
            </div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() => setAddOpen(true)}
          >
            <div className="jp-Dialog-buttonLabel">Add server</div>
          </button>
        </div>
      </div>

      <div className="nbi-info-banner" role="note">
        These are Claude Code's own MCP servers (read from{' '}
        <code>~/.claude.json</code>, project <code>.mcp.json</code>, and the
        per-user-per-project block). Independent of the NBI MCP Servers tab.
      </div>

      {error && (
        <div className="nbi-skills-error" role="alert">
          {error}
        </div>
      )}

      {SCOPES.map(scope => (
        <ClaudeMCPScopeSection
          key={scope}
          scope={scope}
          servers={grouped[scope]}
          loading={loading}
          pendingRemoval={pendingRemoval}
          onRemove={handleRemove}
        />
      ))}

      {addOpen && (
        <ClaudeMCPAddDialog
          onCancel={() => setAddOpen(false)}
          onSubmit={handleAddSubmit}
        />
      )}
    </div>
  );
}

function ClaudeMCPScopeSection(props: {
  scope: ClaudeMCPScope;
  servers: IClaudeMCPServer[];
  loading: boolean;
  pendingRemoval: IClaudeMCPServer | null;
  onRemove: (srv: IClaudeMCPServer) => void;
}) {
  return (
    <div className="nbi-skills-section">
      <div
        className="nbi-skills-section-caption"
        title={SCOPE_HINT[props.scope]}
      >
        {props.scope.toUpperCase()}
      </div>
      {props.servers.length === 0 ? (
        <div className="nbi-skills-empty">
          {props.loading ? 'Loading…' : 'No servers in this scope.'}
        </div>
      ) : (
        props.servers.map(srv => (
          <ClaudeMCPRow
            key={`${srv.scope}-${srv.name}`}
            srv={srv}
            removing={
              props.pendingRemoval?.name === srv.name &&
              props.pendingRemoval?.scope === srv.scope
            }
            onRemove={() => props.onRemove(srv)}
          />
        ))
      )}
    </div>
  );
}

function ClaudeMCPRow(props: {
  srv: IClaudeMCPServer;
  removing: boolean;
  onRemove: () => void;
}) {
  const { srv } = props;
  const summary =
    srv.transport === 'stdio'
      ? [srv.command, ...srv.args].filter(Boolean).join(' ')
      : srv.url;
  return (
    <div className="nbi-skill-row">
      <div className="nbi-skill-row-main">
        <div className="nbi-skill-row-name">{srv.name}</div>
        <div className="nbi-skill-row-description">
          <code>{srv.transport}</code>
          {summary && <span> — {summary}</span>}
        </div>
      </div>
      <div className="nbi-skill-row-actions" onClick={e => e.stopPropagation()}>
        <button
          className="jp-Dialog-button jp-mod-reject jp-mod-styled"
          onClick={props.onRemove}
          disabled={props.removing}
        >
          <div className="jp-Dialog-buttonLabel">
            {props.removing ? 'Removing…' : 'Remove'}
          </div>
        </button>
      </div>
    </div>
  );
}

function ClaudeMCPAddDialog(props: {
  onCancel: () => void;
  onSubmit: (input: IClaudeMCPAddInput) => Promise<void>;
}) {
  const [scope, setScope] = useState<ClaudeMCPScope>('user');
  const [transport, setTransport] = useState<ClaudeMCPTransport>('stdio');
  const [name, setName] = useState('');
  const [commandOrUrl, setCommandOrUrl] = useState('');
  const [argsText, setArgsText] = useState('');
  const [envText, setEnvText] = useState('');
  const [headersText, setHeadersText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const canSubmit = name.trim() && commandOrUrl.trim() && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) {
      return;
    }
    const argsList = argsText
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean);
    const parseKVLines = (
      text: string,
      separator: string
    ): Record<string, string> => {
      const out: Record<string, string> = {};
      for (const line of text.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        const idx = trimmed.indexOf(separator);
        if (idx > 0) {
          out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
        }
      }
      return out;
    };
    const envMap = parseKVLines(envText, '=');
    const headersMap = parseKVLines(headersText, ':');
    setSubmitError(null);
    setSubmitting(true);
    try {
      await props.onSubmit({
        name: name.trim(),
        scope,
        transport,
        commandOrUrl: commandOrUrl.trim(),
        args: transport === 'stdio' ? argsList : undefined,
        env: transport === 'stdio' ? envMap : undefined,
        headers: transport === 'stdio' ? undefined : headersMap
      });
    } catch (e: any) {
      setSubmitError(e?.message ?? String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="nbi-modal-backdrop" onClick={props.onCancel}>
      <div
        className="nbi-modal-card"
        role="dialog"
        aria-modal="true"
        onClick={e => e.stopPropagation()}
      >
        <div className="nbi-modal-title">Add MCP server</div>
        <div className="nbi-modal-body">
          <div className="nbi-form-field">
            <label>Name</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="my-server"
              autoFocus
            />
          </div>
          <div className="nbi-form-field">
            <label>Scope</label>
            <select
              value={scope}
              onChange={e => setScope(e.target.value as ClaudeMCPScope)}
            >
              {SCOPES.map(s => (
                <option key={s} value={s}>
                  {s} — {SCOPE_HINT[s]}
                </option>
              ))}
            </select>
          </div>
          <div className="nbi-form-field">
            <label>Transport</label>
            <select
              value={transport}
              onChange={e => setTransport(e.target.value as ClaudeMCPTransport)}
            >
              {TRANSPORTS.map(t => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="nbi-form-field">
            <label>{transport === 'stdio' ? 'Command' : 'URL'}</label>
            <input
              type="text"
              value={commandOrUrl}
              onChange={e => setCommandOrUrl(e.target.value)}
              placeholder={
                transport === 'stdio' ? 'npx' : 'https://example.com/mcp'
              }
            />
          </div>
          {transport === 'stdio' && (
            <div className="nbi-form-field">
              <label>Args (one per line)</label>
              <textarea
                rows={3}
                value={argsText}
                onChange={e => setArgsText(e.target.value)}
                placeholder={'-y\n@scope/package@latest'}
              />
            </div>
          )}
          {transport === 'stdio' && (
            <div className="nbi-form-field">
              <label>Environment (KEY=value, one per line)</label>
              <textarea
                rows={3}
                value={envText}
                onChange={e => setEnvText(e.target.value)}
                placeholder="API_KEY=…"
              />
            </div>
          )}
          {transport !== 'stdio' && (
            <div className="nbi-form-field">
              <label>Headers (Name: value, one per line)</label>
              <textarea
                rows={3}
                value={headersText}
                onChange={e => setHeadersText(e.target.value)}
                placeholder="Authorization: Bearer …"
              />
            </div>
          )}
          {submitError && (
            <div className="nbi-skills-error" role="alert">
              {submitError}
            </div>
          )}
        </div>
        <div className="nbi-modal-actions">
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={props.onCancel}
            disabled={submitting}
          >
            <div className="jp-Dialog-buttonLabel">Cancel</div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            <div className="jp-Dialog-buttonLabel">
              {submitting ? 'Adding…' : 'Add'}
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}
