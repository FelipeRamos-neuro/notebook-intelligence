# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Issue #441: every command the backend drives must be allowlisted.

The chat sidebar refuses a streamed `commandId` that is not in
`RUN_UI_COMMAND_ALLOWLIST` (`src/command-ids.ts`). That gate only holds if
the list keeps up with the callers, and the obvious failure mode of adding
an allowlist is that someone later adds a `run_ui_command` call and the
feature silently stops working in the frontend with a refusal in the
console.

This test is in pytest rather than jest because only this side can see both
halves: the Python call sites and the TypeScript constant. It parses the
constant as text rather than importing it, which is the same extraction
used to build the list in the first place.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMAND_IDS_TS = REPO_ROOT / "src" / "command-ids.ts"
PYTHON_PACKAGE = REPO_ROOT / "notebook_intelligence"


def _command_id_constants(source: str) -> dict:
    """Map `CommandIDs` member names to their string values."""
    return dict(re.findall(r"export const (\w+) =\s*'([^']+)'", source))


def _allowlisted_ids(source: str) -> set:
    """Resolve RUN_UI_COMMAND_ALLOWLIST to the literal ids it contains."""
    constants = _command_id_constants(source)
    block = source.split("RUN_UI_COMMAND_ALLOWLIST")[1].split("]);")[0]
    ids = set()
    for member in re.findall(r"CommandIDs\.(\w+)", block):
        assert member in constants, f"allowlist names unknown CommandIDs.{member}"
        ids.add(constants[member])
    # Bare string entries, i.e. the JupyterLab commands the tools drive.
    ids.update(re.findall(r"^\s*'([^']+)'", block, re.M))
    return ids


def _run_ui_command_call_sites() -> tuple:
    """Parse every `run_ui_command` call in the package.

    Returns `(ids, unparsed)`. `ids` holds the literal command ids found,
    from both the single-line form and the wrapped form where the id sits
    on the following line (roughly a third of the call sites).

    `unparsed` holds call sites these two regexes could not read, and the
    guard test asserts it is empty. That matters because the parser is
    deliberately narrow and is blind to at least two legal shapes: a
    keyword argument (`run_ui_command(command_id='...')`) and a variable or
    constant holding the id. Without this list such a call site would be
    dropped in silence, and the two allowlist assertions would keep passing
    while no longer covering it. Failing loudly on an unreadable call site
    is the point: the fix is to teach the parser that shape, not to widen
    it.

    Mentions inside comments are skipped rather than reported, because
    `extension.py` discusses `run_ui_command()` in prose and that is not a
    call site. The check is positional (a `#` earlier on the line), so a
    `#` inside a string literal ahead of a real call on the same line would
    skip it; no call site is written that way today.
    """
    ids = set()
    unparsed = []
    for path in sorted(PYTHON_PACKAGE.rglob("*.py")):
        if "labextension" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "run_ui_command(" not in line:
                continue
            if any(
                skip in line
                for skip in (
                    "def run_ui_command",
                    "prepare_run_ui_command",
                    "wait_for_run_ui_command",
                )
            ):
                continue
            call = line.find("run_ui_command(")
            comment = line.find("#")
            if comment != -1 and comment < call:
                continue
            inline = re.search(r"run_ui_command\(\s*'([^']+)'", line)
            if inline:
                ids.add(inline.group(1))
                continue
            # Anchored, so a trailing comment that happens to contain a
            # quoted word cannot pose as the argument.
            if line.rstrip().endswith("run_ui_command(") and index + 1 < len(lines):
                wrapped = re.match(r"\s*'([^']+)'", lines[index + 1])
                if wrapped:
                    ids.add(wrapped.group(1))
                    continue
            unparsed.append(
                f"{path.relative_to(REPO_ROOT)}:{index + 1}: {line.strip()}"
            )
    return ids, unparsed


def _server_driven_ids() -> set:
    """Every command id passed to `run_ui_command` in the package."""
    return _run_ui_command_call_sites()[0]


@pytest.fixture(scope="module")
def sources():
    assert COMMAND_IDS_TS.exists(), f"missing {COMMAND_IDS_TS}"
    return COMMAND_IDS_TS.read_text(encoding="utf-8")


def test_extraction_finds_the_call_sites():
    """Guard the guard: a parser that silently matches nothing would make
    every assertion below vacuously true."""
    found, unparsed = _run_ui_command_call_sites()

    assert len(found) > 15, f"only found {len(found)} ids; the parser is likely broken"
    # Two ids only appear in the wrapped form, so finding them proves that
    # branch of the parser works rather than just the single-line one.
    assert "notebook-intelligence:create-new-notebook" in found
    assert "notebook-intelligence:list-available-notebook-kernels" in found
    # Every call site must be readable, or the allowlist assertions below
    # quietly stop covering the ones that are not.
    assert unparsed == [], (
        "these run_ui_command call sites could not be parsed, so their "
        "command ids are not being checked against the allowlist; teach "
        f"_run_ui_command_call_sites their shape: {unparsed}"
    )


def test_every_server_driven_command_is_allowlisted(sources):
    """The direction that breaks a feature when it drifts.

    A backend caller whose id is missing gets a refusal in the frontend and
    an error string back on its callback, with nothing failing at build time.
    """
    missing = _server_driven_ids() - _allowlisted_ids(sources)

    assert missing == set(), (
        "these ids are sent by run_ui_command but absent from "
        f"RUN_UI_COMMAND_ALLOWLIST in src/command-ids.ts: {sorted(missing)}"
    )


def test_allowlist_does_not_carry_ids_nothing_sends(sources):
    """The other direction, which widens the gate for no reason.

    Not a security hole on its own, but an allowlist that accumulates unused
    entries stops describing what the backend does and drifts toward being
    every command id.
    """
    unused = _allowlisted_ids(sources) - _server_driven_ids()

    assert unused == set(), (
        "these ids are allowlisted but no run_ui_command call sends them; "
        f"remove them or the list stops meaning anything: {sorted(unused)}"
    )


def test_the_three_wrapper_call_sites_are_still_present():
    """A smoke test for wrapper removal. NOT a security boundary.

    Read this before trusting it. Nothing imports the 4,000-line
    chat-sidebar, so the jest tests exercise the wrapper in isolation and a
    sink that stopped calling it went unnoticed: mutation testing showed
    deleting a sink's gate killed zero tests. Counting the call sites closes
    that specific hole, and only that one.

    What it does NOT catch, measured rather than assumed:

    - `const id = commandId; commands.execute(id, args)` in place of the
      wrapper. A real bypass, and `id` is an ordinary name for that variable.
    - `commands?.execute(...)` or an aliased `execute`, invisible to any
      substring search, and addable alongside the untouched legitimate calls
      so no count changes.
    - A template literal id, which a first-character check reads as a
      literal whether or not it interpolates.

    An earlier version of this test also asserted that every
    `commands.execute` first argument was a literal or the wrapper's `id`
    parameter. That was removed: renaming the arrow's own parameter, a
    change that keeps the gate fully intact, failed the assertion with a
    message claiming a streamed id could reach execute ungated. A guard that
    tells a maintainer they introduced a vulnerability when they did not is
    worse than no guard.

    Enforcement lives in the wrapper, which the jest tests cover directly.
    This only notices if a sink stops calling it.
    """
    sidebar = (REPO_ROOT / "src" / "chat-sidebar.tsx").read_text(encoding="utf-8")

    wrapper_calls = sidebar.count("executeResponseStreamCommand(")

    assert wrapper_calls == 3, (
        "expected three executeResponseStreamCommand call sites in "
        "chat-sidebar.tsx (the streamed-button helper and the two "
        f"RunUICommand branches); found {wrapper_calls}. If a sink was "
        "removed on purpose, update this count; if not, a streamed command "
        "id may now reach commands.execute without the allowlist (#441)"
    )


def test_jupyterlab_commands_are_listed_as_bare_strings(sources):
    """`docmanager:*` are not NBI ids, so they cannot come from CommandIDs.

    Pins the reason the allowlist is not simply built from that namespace.
    """
    allowlisted = _allowlisted_ids(sources)

    assert "docmanager:save" in allowlisted
    assert "docmanager:open" in allowlisted
