"""Regression tests for the Claude-mode tool-call surfacing helpers
(see `notebook_intelligence/claude.py`).

Three pure surfaces are pinned here:

1. `humanize_claude_tool_name` — the known-tool map, the
   `mcp__<server>__<tool>` wrapper-stripping, and the unknown-tool
   sentence-case fallback. Failures turn into raw kebab-case identifiers
   on the tool-call cards.
2. `claude_tool_kind` — the read/edit/execute/other categorization that
   picks each card's icon, including the whole-token heuristic for
   unfamiliar MCP tools.
3. `ToolCallData` — the dataclass shape and defaults streamed to the
   frontend.

The worker loop's tool-block dispatch (which calls these helpers and
emits `ToolCallData`) lives inside a deeply-nested closure and is covered
at the integration level rather than here.
"""

from notebook_intelligence.api import ResponseStreamDataType, ToolCallData
from notebook_intelligence.claude import (
    claude_tool_kind,
    extract_tool_diffs,
    humanize_claude_tool_name,
)


class TestHumanizeClaudeToolName:
    def test_known_nbi_tool_maps_to_friendly_label(self):
        assert humanize_claude_tool_name("run-cell") == "Running cell"
        assert humanize_claude_tool_name("add-code-cell") == "Adding code cell"
        assert humanize_claude_tool_name("save-notebook") == "Saving notebook"

    def test_known_claude_builtin_maps_to_friendly_label(self):
        # Claude's built-ins keep CamelCase names through the SDK; the
        # map covers them so the indicator says "Running shell command"
        # not "Bash".
        assert humanize_claude_tool_name("Bash") == "Running shell command"
        assert humanize_claude_tool_name("Read") == "Reading file"
        assert humanize_claude_tool_name("Edit") == "Editing file"

    def test_mcp_wrapper_is_stripped_when_inner_is_known(self):
        # MCP server tools surface to the agent as
        # `mcp__<server>__<tool>`. The label map keys are the inner
        # names; stripping the wrapper before lookup means NBI's own
        # MCP-routed tools still resolve.
        assert (
            humanize_claude_tool_name("mcp__nbi__add-code-cell")
            == "Adding code cell"
        )

    def test_mcp_wrapper_strip_falls_back_when_inner_is_unknown(self):
        # An unknown inner name still gets the sentence-case treatment
        # (not the bare mcp__ prefix), so unknown MCP servers surface
        # readably.
        result = humanize_claude_tool_name("mcp__custom__do-something")
        assert result == "Do something"

    def test_unknown_kebab_name_falls_back_to_sentence_case(self):
        assert (
            humanize_claude_tool_name("future-builtin-tool")
            == "Future builtin tool"
        )

    def test_unknown_snake_name_falls_back_to_sentence_case(self):
        assert humanize_claude_tool_name("future_tool") == "Future tool"

    def test_empty_string_returns_input_unchanged(self):
        # Pathological: SDK shouldn't yield an empty name, but if it
        # does we should hand back the raw value rather than producing
        # the empty string in the indicator.
        assert humanize_claude_tool_name("") == ""

    def test_camelcase_unknown_is_returned_unchanged(self):
        # Unknown CamelCase has no separator to humanize; preserving the
        # original is the least surprising fallback.
        assert humanize_claude_tool_name("Foo") == "Foo"


class TestClaudeToolKind:
    def test_known_tools_map_to_their_kind(self):
        assert claude_tool_kind("Read") == "read"
        assert claude_tool_kind("get-cell-output") == "read"
        assert claude_tool_kind("Edit") == "edit"
        assert claude_tool_kind("add-code-cell") == "edit"
        assert claude_tool_kind("Bash") == "execute"
        assert claude_tool_kind("run-cell") == "execute"
        assert claude_tool_kind("Task") == "other"

    def test_mcp_wrapper_is_unwrapped_for_known_inner(self):
        assert claude_tool_kind("mcp__nbi__run-cell") == "execute"

    def test_heuristic_categorizes_unknown_tools_by_verb_token(self):
        assert claude_tool_kind("mcp__srv__execute_query") == "execute"
        assert claude_tool_kind("create_dashboard") == "edit"
        assert claude_tool_kind("list_tables") == "read"

    def test_heuristic_matches_whole_tokens_not_substrings(self):
        # "widget" must not read as "get"; "command" must not read as "and".
        assert claude_tool_kind("frobnicate_widget") == "other"

    def test_heuristic_precedence_is_execute_then_edit_then_read(self):
        # A name carrying tokens from multiple buckets resolves by the
        # execute -> edit -> read order, so reordering the checks would
        # change these results.
        assert claude_tool_kind("save-and-run-cell") == "execute"
        assert claude_tool_kind("get-and-delete") == "edit"

    def test_unrecognized_tool_falls_back_to_other(self):
        assert claude_tool_kind("frobnicate") == "other"
        assert claude_tool_kind("") == "other"


class TestExtractToolDiffs:
    def test_edit_yields_remove_and_add_lines(self):
        diffs = extract_tool_diffs(
            "Edit",
            {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"},
        )
        assert len(diffs) == 1
        assert diffs[0]["path"] == "a.py"
        types = {line["type"] for line in diffs[0]["lines"]}
        assert "remove" in types and "add" in types
        assert diffs[0]["truncated"] is False

    def test_write_is_all_additions(self):
        diffs = extract_tool_diffs(
            "Write", {"file_path": "new.py", "content": "line1\nline2"}
        )
        assert len(diffs) == 1
        assert [line["type"] for line in diffs[0]["lines"]] == ["add", "add"]
        assert [line["content"] for line in diffs[0]["lines"]] == ["line1", "line2"]

    def test_multiedit_yields_one_diff_per_edit(self):
        diffs = extract_tool_diffs(
            "MultiEdit",
            {
                "file_path": "a.py",
                "edits": [
                    {"old_string": "a", "new_string": "b"},
                    {"old_string": "c", "new_string": "d"},
                ],
            },
        )
        assert len(diffs) == 2
        assert all(d["path"] == "a.py" for d in diffs)

    def test_mcp_wrapped_edit_is_unwrapped(self):
        diffs = extract_tool_diffs(
            "mcp__srv__Edit",
            {"file_path": "a.py", "old_string": "a", "new_string": "b"},
        )
        assert len(diffs) == 1

    def test_non_edit_tool_has_no_diffs(self):
        assert extract_tool_diffs("Read", {"file_path": "a.py"}) == []
        assert extract_tool_diffs("Bash", {"command": "ls"}) == []

    def test_non_dict_input_is_safe(self):
        assert extract_tool_diffs("Edit", None) == []
        assert extract_tool_diffs("Edit", "not a dict") == []

    def test_large_edit_is_truncated(self):
        big = "\n".join(f"line {i}" for i in range(500))
        diffs = extract_tool_diffs(
            "Write", {"file_path": "big.py", "content": big}
        )
        assert diffs[0]["truncated"] is True
        assert len(diffs[0]["lines"]) <= 60

    def test_context_lines_are_classified_and_stripped(self):
        # A change surrounded by shared lines yields context lines whose
        # content must not keep the diff's leading marker space.
        diffs = extract_tool_diffs(
            "Edit",
            {"file_path": "a.py", "old_string": "a\nx\nc", "new_string": "a\ny\nc"},
        )
        lines = diffs[0]["lines"]
        context = [ln for ln in lines if ln["type"] == "context"]
        assert context, "expected at least one context line"
        assert all(not ln["content"].startswith(" ") for ln in context)
        assert {"a", "c"} <= {ln["content"] for ln in context}

    def test_total_diff_lines_capped_across_multiedit(self):
        # Many edits, each large: the cap is across the whole card, not per edit.
        big = "\n".join(f"x{i}" for i in range(100))
        diffs = extract_tool_diffs(
            "MultiEdit",
            {
                "file_path": "a.py",
                "edits": [{"old_string": "", "new_string": big} for _ in range(5)],
            },
        )
        total = sum(len(d["lines"]) for d in diffs)
        assert total <= 60
        assert diffs[-1]["truncated"] is True


class TestToolCallData:
    def test_data_type_is_tool_call(self):
        data = ToolCallData(id="t1", title="Reading file", kind="read", status="in_progress")
        assert data.data_type == ResponseStreamDataType.ToolCall

    def test_defaults_are_in_progress_other(self):
        data = ToolCallData()
        assert data.kind == "other"
        assert data.status == "in_progress"
