"""Regression tests for the built-in file read tool output cap."""

import asyncio
import re

import pytest

import notebook_intelligence.built_in_toolsets as toolsets
from notebook_intelligence import util as util_mod


@pytest.fixture
def jupyter_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(util_mod, "_jupyter_root_dir", str(root))
    return root


def _read_file(file_path: str, **kwargs) -> str:
    tool = toolsets.read_file._tool_function
    return asyncio.run(tool(file_path=file_path, **kwargs))


def _parse_truncation_marker(result: str) -> tuple[int, int]:
    match = re.search(
        r"\[output truncated within line (\d+) column (\d+)\]$",
        result,
    )
    assert match is not None
    return int(match.group(1)), int(match.group(2))


def _visible_content(result: str) -> str:
    return result.split(":\n", 1)[1].rsplit(
        "\n[output truncated within line ",
        1,
    )[0]


class TestReadFileOutputCap:
    def test_public_schema_hides_max_output_tokens(self):
        properties = toolsets.read_file.schema["function"]["parameters"]["properties"]
        assert "max_output_tokens" not in properties

    def test_small_file_is_returned_verbatim(self, jupyter_root):
        target = jupyter_root / "small.txt"
        target.write_text("hello\nworld\n", encoding="utf-8")

        result = _read_file("small.txt")

        assert result == "Content of 'small.txt' (lines 1-2):\nhello\nworld\n"
        assert "[output truncated]" not in result

    def test_oversize_output_is_truncated_with_marker(self, jupyter_root):
        target = jupyter_root / "huge.txt"
        target.write_text("a" * 40_500, encoding="utf-8")

        result = _read_file("huge.txt")
        line, column = _parse_truncation_marker(result)
        visible = _visible_content(result)
        prefix = "Content of 'huge.txt' (lines 1-1):\n"

        assert result.startswith("Content of 'huge.txt' (lines 1-1):\n")
        assert line == 1
        assert column == len(visible) + 1
        assert "a" * 40_500 not in result
        assert len((prefix + visible).encode("utf-8")) <= 40_000

    def test_marker_is_appended_beyond_prefix_plus_content_budget(self, jupyter_root):
        target = jupyter_root / "budget.txt"
        target.write_text("a" * 50_000, encoding="utf-8")

        result = _read_file("budget.txt")
        line, column = _parse_truncation_marker(result)
        visible = _visible_content(result)
        prefix = "Content of 'budget.txt' (lines 1-1):\n"
        marker = f"\n[output truncated within line {line} column {column}]"

        assert visible == "a" * (40_000 - len(prefix.encode("utf-8")))
        assert column == len(visible) + 1
        assert len((prefix + visible).encode("utf-8")) == 40_000
        assert len(result.encode("utf-8")) == 40_000 + len(marker.encode("utf-8"))

    def test_multibyte_utf8_content_keeps_maximum_prefix_within_budget(
        self,
        jupyter_root,
    ):
        target = jupyter_root / "emoji.txt"
        target.write_text("🙂" * 20_000, encoding="utf-8")

        result = _read_file("emoji.txt")
        line, column = _parse_truncation_marker(result)
        visible = _visible_content(result)
        prefix = "Content of 'emoji.txt' (lines 1-1):\n"
        content_budget = 40_000 - len(prefix.encode("utf-8"))
        expected_count = content_budget // len("🙂".encode("utf-8"))

        assert len((prefix + visible).encode("utf-8")) <= 40_000
        assert visible == "🙂" * expected_count
        assert line == 1
        assert column == expected_count + 1

    def test_truncation_marker_tracks_line_and_column_across_newlines(
        self,
        jupyter_root,
    ):
        target = jupyter_root / "many-lines.txt"
        line_text = "123456789\n"
        target.write_text(line_text * 20_000, encoding="utf-8")
        start_line = 10

        result = _read_file("many-lines.txt", start_line=start_line)
        line, column = _parse_truncation_marker(result)
        visible = _visible_content(result)
        prefix = "Content of 'many-lines.txt' (lines 10-20000):\n"
        content_budget = 40_000 - len(prefix.encode("utf-8"))
        full_lines, remainder = divmod(content_budget, len(line_text))
        expected_line = start_line + full_lines
        expected_column = 1 if remainder == 0 else remainder + 1

        assert visible == (line_text * full_lines) + line_text[:remainder]
        assert (line, column) == (expected_line, expected_column)

    def test_non_utf8_file_returns_encoding_error(self, jupyter_root):
        target = jupyter_root / "latin1.bin"
        target.write_bytes(b"\xff\xfe\xfd\xfc")

        result = _read_file("latin1.bin")

        assert "is not a text file or uses an unsupported encoding" in result
        assert "[output truncated within line " not in result
