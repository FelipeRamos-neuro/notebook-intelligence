# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import os
import subprocess
import sys
import types

import pytest

from notebook_intelligence.util import import_litellm


@pytest.mark.timeout(120)
def test_import_does_not_load_provider_sdks():
    """Importing the server extension must not import any provider SDK.

    The provider SDKs (litellm alone is over a second) roughly double the
    import time of notebook_intelligence, and users on a single provider pay
    for all of them; see issue #370. Runs in a subprocess so the check sees a
    clean interpreter rather than whatever the test session already imported;
    the raised timeout covers a cold-cache import on a contended runner.
    """
    code = (
        "import sys\n"
        "import notebook_intelligence\n"
        "loaded = [m for m in ('litellm', 'openai', 'ollama', 'anthropic')"
        " if m in sys.modules]\n"
        "assert not loaded, f'provider SDKs imported eagerly: {loaded}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=110
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def stub_litellm(monkeypatch):
    """Keep the env-contract tests off litellm's real import.

    litellm reads LITELLM_LOCAL_MODEL_COST_MAP only once, at its first import,
    so its behavior is not re-testable in-process anyway; the stub also keeps
    the opt-out test from triggering litellm's remote cost-map fetch when it
    happens to run as the first litellm import of the session.
    """
    monkeypatch.setitem(sys.modules, "litellm", types.ModuleType("litellm"))


def test_import_litellm_defaults_to_local_model_cost_map(monkeypatch, stub_litellm):
    """Pins the helper's env contract, not litellm's read of it."""
    # setenv-then-delenv makes monkeypatch restore the pre-test state
    # afterwards, so the value written by setdefault cannot leak into later
    # tests.
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "placeholder")
    monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP")
    import_litellm()
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "true"


def test_import_litellm_respects_explicit_opt_out(monkeypatch, stub_litellm):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "false")
    import_litellm()
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "false"
