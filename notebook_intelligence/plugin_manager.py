# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Wrapper around Claude Code's `claude plugin` CLI.

Plugin state is owned by Claude Code itself (under `~/.claude/plugins/`).
NBI shells out for both reads and writes:

  - `claude plugin list --json` → JSON array of installed plugins
  - `claude plugin marketplace list --json` → JSON array of marketplaces
  - `claude plugin install <plugin>[@marketplace] -s <scope>`
  - `claude plugin uninstall <plugin> -s <scope> -y`  (-y for non-TTY)
  - `claude plugin enable <plugin> [-s scope]`
  - `claude plugin disable <plugin> [-s scope]`
  - `claude plugin marketplace add <source> [--scope <scope>]`
  - `claude plugin marketplace remove <name>`

The CLI's JSON-output schema is not formally documented; the manager
forwards parsed objects to the frontend mostly untouched so newer Claude
releases that add fields don't get truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Any, Literal, Optional

from notebook_intelligence.util import resolve_claude_cli_path

log = logging.getLogger(__name__)

PluginScope = Literal["user", "project", "local"]
VALID_SCOPES: tuple[PluginScope, ...] = ("user", "project", "local")

CLI_TIMEOUT_SECONDS = 60.0
# Marketplace-add fetches the source over the network (git clone, HTTPS
# manifest pull) so it can outlast the default budget on slow links.
CLI_TIMEOUT_MARKETPLACE_ADD_SECONDS = 120.0

# Plugin / command / source values must not start with `-` so they can't be
# parsed as flags through their position in argv.
def _reject_flag_smuggling(label: str, value: str) -> None:
    if value.startswith("-"):
        raise ValueError(f"Invalid {label} {value!r}: leading '-' is not permitted")


# Flags whose value should never appear in logs. Includes the documented
# secret-bearing flags from `claude mcp add --help` even though plugin
# CLIs don't currently use all of them — the cost of a forward-compat
# entry is negligible vs an accidental leak.
_SECRET_BEARING_FLAGS = frozenset(
    [
        "-e",
        "--env",
        "-H",
        "--header",
        "--client-secret",
        "--client-id",
        "--token",
        "--password",
        "--auth",
        "--bearer",
    ]
)


def _redact_argv_for_log(argv: list[str]) -> list[str]:
    """Drop secret-bearing values for logs. Used by both managers."""
    redacted: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(token)
        if token in _SECRET_BEARING_FLAGS:
            skip_next = True
    return redacted


_GITHUB_URL_SCHEMES = ("http://", "https://", "git://", "ssh://")
_GITHUB_PREFIX_SCHEMES = ("github:", "git@github.com:")
_ALLOWED_NON_GITHUB_SCHEMES = ("https://",)
_LOCAL_PREFIXES = (".", "/", "~")


def is_github_marketplace_source(source: str) -> bool:
    """Best-effort detector for sources that resolve via GitHub.

    Matches ``github:owner/repo``, ``https://github.com/...``,
    ``http://github.com/...``, ``git://github.com/...``,
    ``ssh://git@github.com/...``, ``git@github.com:owner/repo``, and bare
    ``owner/repo[.git][/]`` shorthand. Used to gate marketplace-add when
    an admin sets ``allow_github_plugin_import = False``. Leans toward
    detection — false positives are a benign UX gate; false negatives
    bypass the policy.
    """
    s = source.strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith(_GITHUB_PREFIX_SCHEMES):
        return True
    # ssh://git@github.com/owner/repo — `hostname` returns "github.com".
    if lower.startswith(_GITHUB_URL_SCHEMES):
        host = (urllib.parse.urlparse(s).hostname or "").lower()
        return host == "github.com" or host.endswith(".github.com")
    # owner/repo shorthand. Tolerate trailing slash and `.git` suffix;
    # reject anything path-like or whitespace-bearing.
    if "/" in s and not s.startswith(_LOCAL_PREFIXES) and not any(c.isspace() for c in s):
        stripped = s.rstrip("/")
        if stripped.endswith(".git"):
            stripped = stripped[: -len(".git")]
        return stripped.count("/") == 1
    return False


def is_acceptable_marketplace_source(source: str) -> bool:
    """Reject ``http://`` URLs and exotic schemes (file://, ftp://, etc.) so
    marketplace-add can't be aimed at internal-network HTTP endpoints or
    used for SSRF-style probes. Accepts: ``https://`` URLs, ``github:``,
    ``git@github.com:``, ``git://github.com/...``, ``ssh://git@github.com/...``,
    bare ``owner/repo`` shorthand, and local filesystem paths.
    """
    s = source.strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith(_GITHUB_PREFIX_SCHEMES):
        return True
    if lower.startswith(_LOCAL_PREFIXES):
        return True
    if lower.startswith(_ALLOWED_NON_GITHUB_SCHEMES):
        return True
    # git:// and ssh:// are accepted only when the host is GitHub; the
    # detector above already short-circuits the policy gate for these.
    if lower.startswith(("git://", "ssh://")):
        return is_github_marketplace_source(s)
    # Bare scheme-less form (`owner/repo` or `./local/path`).
    if "://" not in s and ":" not in s.split("/", 1)[0]:
        return True
    return False


class PluginManager:
    # Class-level so the lock is shared across all PluginManager instances
    # within this process. Handlers construct a fresh manager per request,
    # so an instance lock would fail to serialize concurrent CLI writes.
    _write_lock = asyncio.Lock()

    def __init__(self):
        pass

    # --- reads ---------------------------------------------------------

    async def list_plugins(self) -> list[dict[str, Any]]:
        out = await self._run_cli(self._cli_argv(["plugin", "list", "--json"]))
        return self._parse_json_array(out, "plugin list")

    async def list_marketplaces(self) -> list[dict[str, Any]]:
        out = await self._run_cli(
            self._cli_argv(["plugin", "marketplace", "list", "--json"])
        )
        return self._parse_json_array(out, "plugin marketplace list")

    # --- writes (CLI shell-outs) ---------------------------------------

    async def install_plugin(
        self, *, plugin: str, scope: PluginScope = "user"
    ) -> None:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        if not plugin:
            raise ValueError("Missing plugin reference")
        _reject_flag_smuggling("plugin", plugin)
        async with self._write_lock:
            await self._run_cli(
                self._cli_argv(["plugin", "install", "--scope", scope, plugin])
            )

    async def uninstall_plugin(
        self, *, plugin: str, scope: PluginScope = "user"
    ) -> None:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        if not plugin:
            raise ValueError("Missing plugin reference")
        _reject_flag_smuggling("plugin", plugin)
        async with self._write_lock:
            # `-y` skips the prune-confirmation prompt that the CLI requires
            # when stdin isn't a TTY (we're explicitly closing stdin).
            await self._run_cli(
                self._cli_argv(
                    ["plugin", "uninstall", "--scope", scope, "-y", plugin]
                )
            )

    async def set_plugin_enabled(
        self, *, plugin: str, enabled: bool, scope: Optional[PluginScope] = None
    ) -> None:
        if not plugin:
            raise ValueError("Missing plugin reference")
        if scope is not None and scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        _reject_flag_smuggling("plugin", plugin)
        argv = ["plugin", "enable" if enabled else "disable"]
        if scope is not None:
            argv.extend(["--scope", scope])
        argv.append(plugin)
        async with self._write_lock:
            await self._run_cli(self._cli_argv(argv))

    async def add_marketplace(
        self,
        *,
        source: str,
        scope: PluginScope = "user",
        allow_github: bool = True,
    ) -> None:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope {scope!r}; expected one of {VALID_SCOPES}")
        if not source:
            raise ValueError("Missing marketplace source")
        _reject_flag_smuggling("source", source)
        if not is_acceptable_marketplace_source(source):
            raise ValueError(
                f"Invalid marketplace source {source!r}: only https://, "
                "git@github.com:, github:, ssh://git@github.com, "
                "git://github.com, owner/repo shorthand, and local paths "
                "are accepted"
            )
        if not allow_github and is_github_marketplace_source(source):
            raise PermissionError(
                "Adding marketplaces from GitHub is disabled by your administrator"
            )
        async with self._write_lock:
            await self._run_cli(
                self._cli_argv(
                    ["plugin", "marketplace", "add", "--scope", scope, source]
                ),
                timeout=CLI_TIMEOUT_MARKETPLACE_ADD_SECONDS,
            )

    async def remove_marketplace(self, *, name: str) -> None:
        if not name:
            raise ValueError("Missing marketplace name")
        _reject_flag_smuggling("name", name)
        async with self._write_lock:
            await self._run_cli(
                self._cli_argv(["plugin", "marketplace", "remove", name])
            )

    # --- internals -----------------------------------------------------

    def _cli_argv(self, tail: list[str]) -> list[str]:
        cli = resolve_claude_cli_path()
        if not cli:
            raise FileNotFoundError(
                "Claude Code CLI not found on PATH (set NBI_CLAUDE_CLI_PATH or install `claude`)"
            )
        return [cli, *tail]

    # Keys we'll walk to find an array of objects when the CLI returns a
    # wrapper shape. Tolerates the documented `{"installed": [...]}` form
    # and the obvious near-future variants without hard-coding.
    _LIST_WRAPPER_KEYS = ("installed", "plugins", "marketplaces", "items", "data")

    @staticmethod
    def _parse_json_array(out: str, what: str) -> list[dict[str, Any]]:
        text = out.strip()
        if not text:
            return []
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse {what} JSON output: {exc}") from exc
        if isinstance(doc, list):
            return [d for d in doc if isinstance(d, dict)]
        if isinstance(doc, dict):
            for key in PluginManager._LIST_WRAPPER_KEYS:
                value = doc.get(key)
                if isinstance(value, list):
                    return [d for d in value if isinstance(d, dict)]
        # The CLI returned a non-empty doc we couldn't recognize. Surfacing
        # an error beats returning `[]` and rendering "no plugins installed"
        # — that would silently mask a CLI version skew.
        raise ValueError(
            f"Unrecognized JSON shape from {what}; the Claude CLI may have rev'd"
        )

    async def _run_cli(
        self, argv: list[str], *, timeout: float = CLI_TIMEOUT_SECONDS
    ) -> str:
        log.info(
            "claude plugin invocation: %s",
            " ".join(_redact_argv_for_log(argv[1:])),
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"`claude plugin` timed out after {timeout}s"
            )
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            msg = (err or out).strip() or f"claude plugin failed (exit {proc.returncode})"
            raise ValueError(msg)
        return out
