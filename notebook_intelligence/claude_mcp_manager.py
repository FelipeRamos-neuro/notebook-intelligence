# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Read/write Claude Code's MCP server configuration.

Reads come from the JSON config files Claude maintains itself:

  - User scope:    ``~/.claude.json`` → top-level ``mcpServers``
  - Local scope:   ``~/.claude.json`` → ``projects.<cwd>.mcpServers``
  - Project scope: ``<cwd>/.mcp.json``

Writes go through ``claude mcp add`` / ``claude mcp remove`` so Claude
remains the source of truth for any side effects (project-trust prompts,
oauth bookkeeping, etc.). The file-based reads are decoupled from the CLI's
``claude mcp list`` output because that command does live health checks
that are slow and unstructured (no ``--json``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional

from notebook_intelligence.util import resolve_claude_cli_path

log = logging.getLogger(__name__)

ClaudeMCPScope = Literal["user", "project", "local"]
VALID_SCOPES: tuple[ClaudeMCPScope, ...] = ("user", "project", "local")
VALID_TRANSPORTS: tuple[str, ...] = ("stdio", "sse", "http")

CLI_TIMEOUT_SECONDS = 30.0

# Reject names / commands / URLs that start with `-` so the user can't
# smuggle a Claude CLI flag through their position in argv.
def _reject_flag_smuggling(*, name: str, command_or_url: str) -> None:
    for label, value in (("name", name), ("command_or_url", command_or_url)):
        if value.startswith("-"):
            raise ValueError(
                f"Invalid {label} {value!r}: leading '-' is not permitted"
            )


def _redact_argv_for_log(argv: list[str]) -> list[str]:
    """Drop secret-bearing values (-e KEY=value, -H NAME: value) for logs."""
    redacted: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(token)
        if token in ("-e", "--env", "-H", "--header"):
            skip_next = True
    return redacted


@dataclass(frozen=True)
class ClaudeMCPServer:
    name: str
    scope: ClaudeMCPScope
    transport: str  # "stdio" | "sse" | "http"
    command: str = ""  # stdio
    args: list[str] = field(default_factory=list)  # stdio
    env: dict[str, str] = field(default_factory=dict)  # stdio
    url: str = ""  # sse | http
    headers: dict[str, str] = field(default_factory=dict)  # sse | http

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "scope": self.scope,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "url": self.url,
            "headers": dict(self.headers),
        }


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to read Claude MCP config %s: %s", path, exc)
        return None


def _coerce_str_dict(d: Optional[dict]) -> dict[str, str]:
    """Stringify keys/values; drop None values so they don't surface as the
    string "None" in the UI."""
    if not isinstance(d, dict):
        return {}
    return {str(k): str(v) for k, v in d.items() if v is not None}


def _coerce_server(name: str, raw: dict, scope: ClaudeMCPScope) -> Optional[ClaudeMCPServer]:
    if not isinstance(raw, dict):
        return None
    transport = raw.get("type") or "stdio"
    if transport not in VALID_TRANSPORTS:
        # Unknown transport — pass through untouched so the UI can show it
        # rather than silently dropping it.
        transport = str(transport)
    return ClaudeMCPServer(
        name=name,
        scope=scope,
        transport=transport,
        command=str(raw.get("command", "")),
        args=list(raw.get("args") or []),
        env=_coerce_str_dict(raw.get("env")),
        url=str(raw.get("url", "")),
        headers=_coerce_str_dict(raw.get("headers")),
    )


def _gather_from_dict(
    block: Optional[dict], scope: ClaudeMCPScope
) -> Iterable[ClaudeMCPServer]:
    if not isinstance(block, dict):
        return ()
    out: list[ClaudeMCPServer] = []
    for name, raw in block.items():
        srv = _coerce_server(str(name), raw, scope)
        if srv is not None:
            out.append(srv)
    return out


class ClaudeMCPManager:
    def __init__(self, working_dir: Optional[str] = None):
        self._working_dir = Path(working_dir) if working_dir else Path.cwd()
        self._user_config_path = Path.home() / ".claude.json"
        # Serialize CLI writes within this process. Claude's CLI does
        # read-modify-write on `~/.claude.json` without a lockfile, so two
        # in-flight `add`s from the same NBI server can clobber.
        self._write_lock = asyncio.Lock()

    # --- reads ---------------------------------------------------------

    def list_servers(self) -> list[ClaudeMCPServer]:
        """Enumerate user + local + project scope servers visible to Claude."""
        servers: list[ClaudeMCPServer] = []
        user_doc = _read_json(self._user_config_path) or {}

        servers.extend(_gather_from_dict(user_doc.get("mcpServers"), "user"))

        # Local scope is keyed by absolute working-dir path under `projects`.
        projects = user_doc.get("projects") or {}
        local_block = (projects.get(str(self._working_dir)) or {}).get("mcpServers")
        servers.extend(_gather_from_dict(local_block, "local"))

        # Project scope: ``<cwd>/.mcp.json`` with top-level ``mcpServers``.
        project_doc = _read_json(self._working_dir / ".mcp.json") or {}
        servers.extend(_gather_from_dict(project_doc.get("mcpServers"), "project"))

        servers.sort(key=lambda s: (s.name, s.scope))
        return servers

    def get_server(self, name: str, scope: ClaudeMCPScope) -> Optional[ClaudeMCPServer]:
        for srv in self.list_servers():
            if srv.name == name and srv.scope == scope:
                return srv
        return None

    # --- writes (CLI shell-outs) ---------------------------------------

    async def add_server(
        self,
        *,
        name: str,
        scope: ClaudeMCPScope,
        transport: str,
        command_or_url: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> ClaudeMCPServer:
        """Run ``claude mcp add`` and return the persisted record."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        if transport not in VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid transport {transport!r}; expected one of {VALID_TRANSPORTS}"
            )
        if not name:
            raise ValueError("Missing server name")
        if not command_or_url:
            raise ValueError("Missing command or URL")
        _reject_flag_smuggling(name=name, command_or_url=command_or_url)

        cmd = self._cli_argv(["mcp", "add", "--scope", scope, "--transport", transport])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        for key, value in (headers or {}).items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(name)
        cmd.append(command_or_url)
        if args:
            cmd.append("--")
            cmd.extend(args)

        async with self._write_lock:
            await self._run_cli(cmd, write_op=True)
        srv = self.get_server(name, scope)
        if srv is None:
            # Surfaced if Claude wrote elsewhere or our reads missed it. The CLI
            # already succeeded, so don't 500 — synthesize from the inputs.
            # Logged as a warning so an operator notices a real read/write
            # divergence (cwd mismatch, scope misroute, race).
            log.warning(
                "claude mcp add succeeded but post-add read missed %r in %s scope; "
                "returning synthesized record",
                name,
                scope,
            )
            return ClaudeMCPServer(
                name=name,
                scope=scope,
                transport=transport,
                command=command_or_url if transport == "stdio" else "",
                args=list(args or []),
                env=dict(env or {}),
                url=command_or_url if transport in ("sse", "http") else "",
                headers=dict(headers or {}),
            )
        return srv

    async def remove_server(self, name: str, scope: ClaudeMCPScope) -> None:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        if not name:
            raise ValueError("Missing server name")
        _reject_flag_smuggling(name=name, command_or_url=name)
        async with self._write_lock:
            await self._run_cli(
                self._cli_argv(["mcp", "remove", "--scope", scope, name]),
                write_op=True,
            )

    # --- internals -----------------------------------------------------

    def _cli_argv(self, tail: list[str]) -> list[str]:
        cli = resolve_claude_cli_path()
        if not cli:
            raise FileNotFoundError(
                "Claude Code CLI not found on PATH (set NBI_CLAUDE_CLI_PATH or install `claude`)"
            )
        return [cli, *tail]

    async def _run_cli(self, argv: list[str], *, write_op: bool) -> str:
        log.info(
            "claude mcp invocation: %s", " ".join(_redact_argv_for_log(argv[1:]))
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._working_dir),
            # Closed stdin keeps Claude from blocking on a project-trust or
            # OAuth prompt; it'll fail with a clean nonzero exit instead of
            # hanging until the timeout.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=CLI_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"`claude mcp` timed out after {CLI_TIMEOUT_SECONDS}s"
            )
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            # Surface Claude's own error message; drop empty stderr.
            msg = (err or out).strip() or f"claude mcp failed (exit {proc.returncode})"
            raise ValueError(msg)
        return out
