"""Wire-shape and scope tests for ClaudeSessionsListHandler.

The handler is a thin wrapper around ``list_all_sessions``, but its
response shape (``{sessions, current_cwd}``) is the only Python↔TypeScript
contract not pinned elsewhere. A silent rename of any of those keys would
break the pickers without any test failing — these tests prevent that.

The scope tests pin the ``?scope=cwd`` filtering behavior added in
issue #188 so the chat sidebar's popover stays scoped to the current cwd
without re-introducing client-side filtering.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import notebook_intelligence.extension as ext_module
from notebook_intelligence.claude_sessions import ClaudeSessionInfo
from notebook_intelligence.extension import ClaudeSessionsListHandler


def _make_handler(scope: str | None = None):
    handler = MagicMock(spec=ClaudeSessionsListHandler)
    handler.request = MagicMock()

    def get_query_argument(name, default=None):
        if name == "scope" and scope is not None:
            return scope
        return default

    handler.get_query_argument = get_query_argument
    return handler


def _parse_response(handler) -> dict:
    return json.loads(handler.finish.call_args[0][0])


@pytest.fixture
def claude_mode_on():
    with patch.object(ext_module, "ai_service_manager") as mock_asm:
        mock_asm.is_claude_code_mode = True
        yield mock_asm


@pytest.fixture
def claude_mode_off():
    with patch.object(ext_module, "ai_service_manager") as mock_asm:
        mock_asm.is_claude_code_mode = False
        yield mock_asm


def _session(
    session_id: str, path: str, cwd: str = ""
) -> ClaudeSessionInfo:
    return ClaudeSessionInfo(
        session_id=session_id,
        path=path,
        modified_at=1.0,
        created_at=0.0,
        preview="hello",
        cwd=cwd,
    )


class TestClaudeSessionsListHandler:
    def test_returns_404_when_claude_code_mode_off(self, claude_mode_off):
        handler = _make_handler()
        ClaudeSessionsListHandler.get(handler)
        handler.set_status.assert_called_with(404)
        body = _parse_response(handler)
        assert "error" in body

    def test_response_shape_pins_python_typescript_contract(
        self, claude_mode_on, tmp_path
    ):
        with patch.object(ext_module, "list_all_claude_sessions", return_value=[]):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=str(tmp_path)
            ):
                handler = _make_handler()
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        assert set(body.keys()) == {"sessions", "current_cwd"}

    def test_serializes_session_info_records(self, claude_mode_on, tmp_path):
        sessions = [_session("abc12345", str(tmp_path / "abc12345.jsonl"))]
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=sessions
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=str(tmp_path)
            ):
                handler = _make_handler()
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        assert len(body["sessions"]) == 1
        assert set(body["sessions"][0].keys()) == {
            "session_id",
            "path",
            "modified_at",
            "created_at",
            "preview",
            "cwd",
        }
        assert body["sessions"][0]["session_id"] == "abc12345"

    def test_handles_missing_cwd_gracefully(self, claude_mode_on):
        with patch.object(ext_module, "list_all_claude_sessions", return_value=[]):
            with patch.object(ext_module, "get_jupyter_root_dir", return_value=None):
                handler = _make_handler()
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        assert body["current_cwd"] == ""

    def test_scope_all_returns_sessions_from_every_project(
        self, claude_mode_on, tmp_path
    ):
        cwd = str(tmp_path / "proj")
        in_cwd = _session("abc", str(tmp_path / "proj" / "abc.jsonl"), cwd=cwd)
        elsewhere = _session(
            "xyz",
            str(tmp_path / "other-project" / "xyz.jsonl"),
            cwd=str(tmp_path / "other-project"),
        )
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=[in_cwd, elsewhere]
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=cwd
            ):
                handler = _make_handler(scope="all")
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        ids = {s["session_id"] for s in body["sessions"]}
        assert ids == {"abc", "xyz"}

    def test_scope_cwd_filters_to_current_sessions_dir(
        self, claude_mode_on, tmp_path
    ):
        # scope=cwd is a realpath compare on the session's cwd field, not
        # on the transcript path. Sessions in another project must drop
        # out; sessions in the same project (even through a symlink alias)
        # stay.
        cwd = str(tmp_path / "proj")
        in_cwd = _session("abc", str(tmp_path / "proj" / "abc.jsonl"), cwd=cwd)
        elsewhere = _session(
            "xyz",
            str(tmp_path / "other-project" / "xyz.jsonl"),
            cwd=str(tmp_path / "other-project"),
        )
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=[in_cwd, elsewhere]
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=cwd
            ):
                handler = _make_handler(scope="cwd")
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        ids = {s["session_id"] for s in body["sessions"]}
        assert ids == {"abc"}

    def test_scope_defaults_to_all_when_omitted(self, claude_mode_on, tmp_path):
        # The launcher tile passes scope='all' explicitly, but a missing
        # query arg should produce the same behavior so curl/test callers
        # don't have to remember the param.
        cwd = str(tmp_path / "proj")
        in_cwd = _session("abc", str(tmp_path / "proj" / "abc.jsonl"), cwd=cwd)
        elsewhere = _session(
            "xyz",
            str(tmp_path / "other-project" / "xyz.jsonl"),
            cwd=str(tmp_path / "other-project"),
        )
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=[in_cwd, elsewhere]
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=cwd
            ):
                handler = _make_handler(scope=None)
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        assert {s["session_id"] for s in body["sessions"]} == {"abc", "xyz"}

    def test_scope_cwd_drops_sessions_with_empty_cwd(
        self, claude_mode_on, tmp_path
    ):
        # A session whose transcript carried no cwd field AND whose
        # project-dir name failed dash-decoding ends up with cwd="".
        # Such a session cannot be realpath-compared against the current
        # cwd, so the handler drops it from scope=cwd. (It stays visible
        # under scope=all so the user can still find it.) Pin the drop
        # so a future refactor doesn't accidentally start including
        # un-matched empty-cwd rows.
        cwd = str(tmp_path / "proj")
        in_cwd = _session("abc", str(tmp_path / "proj" / "abc.jsonl"), cwd=cwd)
        no_cwd = _session("xyz", str(tmp_path / "other" / "xyz.jsonl"), cwd="")
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=[in_cwd, no_cwd]
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=cwd
            ):
                handler = _make_handler(scope="cwd")
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        ids = {s["session_id"] for s in body["sessions"]}
        assert ids == {"abc"}

    def test_scope_cwd_matches_via_symlink_alias(self, claude_mode_on, tmp_path):
        # A symlinked workspace (common on JupyterHub user dirs) means
        # the jupyter root and the session's recorded cwd may differ as
        # strings but resolve to the same realpath. scope=cwd compares
        # realpaths so the picker still finds the user's own sessions.
        target = tmp_path / "real"
        target.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(target)

        in_cwd = _session(
            "abc",
            str(target / "abc.jsonl"),
            cwd=str(target),
        )
        with patch.object(
            ext_module, "list_all_claude_sessions", return_value=[in_cwd]
        ):
            with patch.object(
                ext_module, "get_jupyter_root_dir", return_value=str(alias)
            ):
                handler = _make_handler(scope="cwd")
                ClaudeSessionsListHandler.get(handler)

        body = _parse_response(handler)
        ids = {s["session_id"] for s in body["sessions"]}
        assert ids == {"abc"}
