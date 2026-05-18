# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Schema validation for the user-supplied MCP config payload.

``MCPConfigFileHandler.post`` writes the request body verbatim to
``user_mcp`` on disk and to the in-memory ``MCPManager``. Without
validation, malformed shapes (``mcpServers: null``, ``mcpServers:
{name: "string"}``) crash downstream code, and adversarial shapes
install stdio servers that fork arbitrary commands on the next session
start. The validator enforces the documented shape so the request fails
loudly at HTTP time instead of corrupting the loader.

Scope: this validator constrains the JSON *shape* only. Any
command-string allowlist or env-key denylist is enforced separately at
server-construction time, so a config that survives validation here may
still be rejected later by the admin policy gate.
"""

from __future__ import annotations

from typing import Iterable

# Top-level keys this handler accepts. Anything else is rejected to
# prevent a future writer from silently round-tripping junk through the
# file on disk.
_ALLOWED_TOP_LEVEL_KEYS: frozenset = frozenset({"mcpServers", "participants"})

# Per-server keys. Matches the shape documented in the README and the
# tail-end of ``MCPManager.create_mcp_server``.
_ALLOWED_SERVER_KEYS: frozenset = frozenset(
    {
        "command",
        "args",
        "env",
        "url",
        "headers",
        "disabled",
        "autoApprove",
        "type",
    }
)

_ALLOWED_TRANSPORT_TYPES: frozenset = frozenset({"stdio", "sse", "http"})


class MCPConfigValidationError(ValueError):
    """Raised when the user-supplied MCP config doesn't match the schema."""


def _require_str(label: str, value) -> None:
    if not isinstance(value, str):
        raise MCPConfigValidationError(
            f"{label} must be a string, got {type(value).__name__}"
        )


def _require_str_list(label: str, value) -> None:
    if not isinstance(value, list):
        raise MCPConfigValidationError(
            f"{label} must be a list of strings, got {type(value).__name__}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise MCPConfigValidationError(
                f"{label}[{i}] must be a string, got {type(item).__name__}"
            )


def _require_str_dict(label: str, value) -> None:
    if not isinstance(value, dict):
        raise MCPConfigValidationError(
            f"{label} must be an object mapping strings to strings, "
            f"got {type(value).__name__}"
        )
    for k, v in value.items():
        if not isinstance(k, str):
            raise MCPConfigValidationError(
                f"{label} key must be a string, got {type(k).__name__}"
            )
        if not isinstance(v, str):
            raise MCPConfigValidationError(
                f"{label}[{k!r}] must be a string, got {type(v).__name__}"
            )


def _validate_server_entry(name: str, entry) -> None:
    label = f"mcpServers[{name!r}]"
    if not isinstance(entry, dict):
        raise MCPConfigValidationError(
            f"{label} must be an object, got {type(entry).__name__}"
        )
    unknown = set(entry) - _ALLOWED_SERVER_KEYS
    if unknown:
        # Reject unknown keys rather than silently dropping them: a typo
        # like ``commnd`` would otherwise leave the server entry empty
        # and the user would see an unhelpful "missing command" error
        # later, instead of a clear "unknown key" rejection here.
        raise MCPConfigValidationError(
            f"{label} has unknown keys: {sorted(unknown)!r}; "
            f"allowed keys are {sorted(_ALLOWED_SERVER_KEYS)!r}"
        )
    has_command = "command" in entry
    has_url = "url" in entry
    is_disabled = entry.get("disabled") is True
    if has_command and has_url:
        raise MCPConfigValidationError(
            f"{label} cannot set both 'command' and 'url'; pick one"
        )
    if not has_command and not has_url and not is_disabled:
        # A disabled-only entry is a legitimate way to park a server
        # config (no command, no url) in the file; the runtime loader
        # skips it before reading either field. Require at least one
        # transport field when the entry is enabled, so a user can't
        # accidentally save an empty entry that silently does nothing.
        raise MCPConfigValidationError(
            f"{label} must set either 'command' (stdio) or 'url' (sse/http) "
            "unless 'disabled' is true"
        )
    if has_command:
        _require_str(f"{label}.command", entry["command"])
        if not entry["command"].strip():
            raise MCPConfigValidationError(f"{label}.command cannot be empty")
        if "args" in entry:
            _require_str_list(f"{label}.args", entry["args"])
        if "env" in entry:
            _require_str_dict(f"{label}.env", entry["env"])
    if has_url:
        _require_str(f"{label}.url", entry["url"])
        if not entry["url"].strip():
            raise MCPConfigValidationError(f"{label}.url cannot be empty")
        if "headers" in entry:
            _require_str_dict(f"{label}.headers", entry["headers"])
    if "disabled" in entry and not isinstance(entry["disabled"], bool):
        raise MCPConfigValidationError(
            f"{label}.disabled must be a boolean"
        )
    if "autoApprove" in entry:
        _require_str_list(f"{label}.autoApprove", entry["autoApprove"])
    if "type" in entry:
        _require_str(f"{label}.type", entry["type"])
        if entry["type"] not in _ALLOWED_TRANSPORT_TYPES:
            raise MCPConfigValidationError(
                f"{label}.type must be one of "
                f"{sorted(_ALLOWED_TRANSPORT_TYPES)!r}; "
                f"got {entry['type']!r}"
            )
        # The loader infers transport from command-vs-url presence and
        # ignores `type` outright. Reject any explicit `type` that
        # contradicts the inferred transport so the user sees the
        # mismatch here rather than wondering why their `type` was
        # silently dropped.
        if entry["type"] == "stdio" and has_url:
            raise MCPConfigValidationError(
                f"{label}.type='stdio' is inconsistent with 'url'"
            )
        if entry["type"] in {"sse", "http"} and has_command:
            raise MCPConfigValidationError(
                f"{label}.type={entry['type']!r} is inconsistent with 'command'"
            )


def _validate_participants(participants) -> None:
    if not isinstance(participants, dict):
        raise MCPConfigValidationError(
            f"participants must be an object, got {type(participants).__name__}"
        )
    for pid, entry in participants.items():
        if not isinstance(pid, str):
            raise MCPConfigValidationError(
                f"participants key must be a string, got {type(pid).__name__}"
            )
        if not isinstance(entry, dict):
            raise MCPConfigValidationError(
                f"participants[{pid!r}] must be an object, got {type(entry).__name__}"
            )
        if "name" in entry:
            _require_str(f"participants[{pid!r}].name", entry["name"])
        if "servers" in entry:
            _require_str_list(f"participants[{pid!r}].servers", entry["servers"])
        if "nbiTools" in entry:
            _require_str_list(f"participants[{pid!r}].nbiTools", entry["nbiTools"])


def validate_mcp_config(data) -> None:
    """Raise ``MCPConfigValidationError`` if ``data`` is not a valid MCP config.

    Accepts an empty dict (``{}``) as a degenerate-valid "clear all"
    payload; the user already manages the file via the Settings dialog
    and the empty case is a legitimate "remove every server" outcome.
    """
    if not isinstance(data, dict):
        raise MCPConfigValidationError(
            f"MCP config must be a JSON object, got {type(data).__name__}"
        )
    unknown_top = set(data) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown_top:
        raise MCPConfigValidationError(
            f"Unknown top-level keys: {sorted(unknown_top)!r}; "
            f"allowed: {sorted(_ALLOWED_TOP_LEVEL_KEYS)!r}"
        )
    if "mcpServers" in data:
        # Distinguish "mcpServers is absent" (degenerate-valid) from
        # "mcpServers is null" (the motivating crash shape from the
        # audit), so an explicitly-null payload doesn't bypass the
        # object-shape check.
        servers = data["mcpServers"]
        if not isinstance(servers, dict):
            raise MCPConfigValidationError(
                f"mcpServers must be an object, got {type(servers).__name__}"
            )
        for name, entry in servers.items():
            if not isinstance(name, str):
                raise MCPConfigValidationError(
                    f"mcpServers key must be a string, got {type(name).__name__}"
                )
            _validate_server_entry(name, entry)
    if "participants" in data:
        _validate_participants(data["participants"])
