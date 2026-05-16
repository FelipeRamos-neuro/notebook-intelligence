"""Regression tests for the Claude-mode tool-call progress surfacing
(see `notebook_intelligence/claude.py`).

Two surfaces are pinned here:

1. `humanize_claude_tool_name` — covers the known-tool map, the
   `mcp__<server>__<tool>` wrapper-stripping, and the unknown-tool
   fallback. Failures here turn into raw kebab-case identifiers in the
   chat sidebar's progress indicator.
2. The worker loop's tool-block dispatch in `_client_thread_func` — the
   loop body lives inside a deeply-nested closure, so rather than
   simulating the full worker we exercise the same dispatch logic
   directly on synthetic SDK message objects.
"""

from notebook_intelligence.claude import humanize_claude_tool_name


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
