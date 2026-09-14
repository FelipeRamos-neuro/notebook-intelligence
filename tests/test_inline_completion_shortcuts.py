# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Issue #442: a Tab binding for accept must require a live suggestion.

JupyterLab applies an accepted inline suggestion by replacing the text
between the cursor recorded when the completion was requested and the
cursor as it stands now, then moving the cursor to the end of what it
inserted (`InlineCompleter.accept` in `@jupyterlab/completer`). Anything
typed after the request was issued sits inside that replaced range.

`.jp-mod-completer-enabled` means only that the completer is available in
this editor, which is true of every code cell in edit mode. The class that
means a suggestion is currently on screen is
`.jp-mod-inline-completer-active`, and it is what JupyterLab's own Tab
binding for this command is gated on. Matching that guard keeps Tab bound
to accept only while there is something to accept, and leaves Tab free to
indent otherwise.

These assertions read the shipped schema rather than the built labextension
copy, since the schema is the file a contributor edits.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SCHEMA = REPO_ROOT / "schema" / "plugin.json"

ACCEPT_COMMAND = "inline-completer:accept"
ACTIVE_SUGGESTION_CLASS = ".jp-mod-inline-completer-active"
MERELY_ENABLED_CLASS = ".jp-mod-completer-enabled"


def _shortcuts() -> list:
    schema = json.loads(PLUGIN_SCHEMA.read_text(encoding="utf-8"))
    return schema["jupyter.lab.shortcuts"]


def test_schema_declares_the_shortcuts_these_tests_check():
    """Guard the guard: an empty or renamed section would make the
    assertions below pass while checking nothing."""
    shortcuts = _shortcuts()

    assert len(shortcuts) >= 3, f"only found {len(shortcuts)} shortcuts"
    assert any(entry.get("command") == ACCEPT_COMMAND for entry in shortcuts), (
        f"no {ACCEPT_COMMAND} binding in the schema; if the binding was "
        "removed on purpose, these tests should go with it"
    )


def test_a_guarded_tab_accept_binding_exists_for_the_notebook():
    """State the requirement positively, or deletion passes.

    Mutation testing caught this rather than review: removing the notebook
    Tab binding outright left every other assertion here green, because the
    other two are phrased as "no unguarded binding exists" and absence
    satisfies them, while the existence check above is still met by the
    other accept binding. A test that passes when the feature is deleted
    pins nothing.
    """
    guarded = [
        entry
        for entry in _shortcuts()
        if entry.get("command") == ACCEPT_COMMAND
        and entry.get("keys") == ["Tab"]
        and ".jp-Notebook" in entry.get("selector", "")
        and ACTIVE_SUGGESTION_CLASS in entry.get("selector", "")
    ]

    assert len(guarded) == 1, (
        "expected exactly one notebook Tab binding for "
        f"{ACCEPT_COMMAND} carrying {ACTIVE_SUGGESTION_CLASS}; found "
        f"{len(guarded)}. Tab accepting an inline suggestion in a notebook "
        "cell is the behaviour #442 narrowed rather than removed, so it "
        "should still be here and still be guarded"
    )


def test_every_tab_accept_binding_requires_an_active_suggestion():
    offenders = [
        entry
        for entry in _shortcuts()
        if entry.get("command") == ACCEPT_COMMAND
        and entry.get("keys") == ["Tab"]
        and ACTIVE_SUGGESTION_CLASS not in entry.get("selector", "")
    ]

    assert offenders == [], (
        "these Tab bindings accept an inline completion without requiring "
        f"{ACTIVE_SUGGESTION_CLASS}, so Tab can reach the accept path when no "
        "suggestion is on screen, and accept() replaces everything typed "
        f"since the request was made (#442): {offenders}"
    )


def test_tab_is_not_bound_on_merely_enabled_completer():
    """The exact shape #442 was reported against.

    Spelled out separately from the assertion above because this is the
    selector that regressed, and a maintainer reading a failure should see
    which class is the problem rather than only that some guard is missing.
    """
    too_broad = [
        entry.get("selector")
        for entry in _shortcuts()
        if entry.get("keys") == ["Tab"]
        and MERELY_ENABLED_CLASS in entry.get("selector", "")
        and ACTIVE_SUGGESTION_CLASS not in entry.get("selector", "")
    ]

    assert too_broad == [], (
        f"{MERELY_ENABLED_CLASS} is true for every code cell in edit mode, so "
        "binding Tab on it alone takes Tab away from indentation and hands it "
        f"to accept; require {ACTIVE_SUGGESTION_CLASS} instead: {too_broad}"
    )
