# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace

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


@pytest.mark.timeout(120)
def test_manager_construction_does_not_load_provider_sdks():
    """Constructing the manager must not import a provider SDK either.

    #370 took the SDKs out of the module import, but AIServiceManager builds
    every provider, and the Ollama provider used to enumerate local models in
    its constructor, which imported ollama and called the Ollama host on every
    server start whatever the configured provider was (#427). Subprocess for
    the same reason as above: a clean interpreter.
    """
    code = (
        "import sys\n"
        "from unittest.mock import Mock, patch\n"
        "import notebook_intelligence.ai_service_manager as asm\n"
        "with patch.object(asm, 'NBIConfig') as config_class:\n"
        "    config = Mock()\n"
        "    config.rules_enabled = False\n"
        "    config.mcp = {'mcpServers': {}, 'participants': {}}\n"
        "    config.user_skills_directory = '/test/user_skills'\n"
        "    config.project_skills_directory = lambda _root: '/test/project_skills'\n"
        # claude_settings and acp_settings must be real dicts: a bare Mock's
        # .get() is truthy, which puts the manager into that agent's mode and
        # tests something else entirely (the claude branch also spawns a model
        # fetch that imports anthropic, racing the assertion below).
        "    config.claude_settings = {}\n"
        "    config.acp_settings = {}\n"
        "    config.chat_model = {'provider': 'none', 'model': 'none'}\n"
        "    config.inline_completion_model = {'provider': 'none', 'model': 'none'}\n"
        "    config.using_github_copilot_service = False\n"
        "    config_class.return_value = config\n"
        "    manager = asm.AIServiceManager({'server_root_dir': '/test'})\n"
        "loaded = [m for m in ('litellm', 'openai', 'ollama', 'anthropic')"
        " if m in sys.modules]\n"
        "assert not loaded, f'provider SDKs imported at startup: {loaded}'\n"
        # Positive control: without it, a future change that stops building
        # the provider at all would satisfy the assertion above while testing
        # nothing.
        "manager.ollama_llm_provider.chat_models\n"
        "assert 'ollama' in sys.modules, 'the deferred path never ran'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=110,
        # The positive control makes a real call, and the assertion only cares
        # that the SDK loaded; point it at a dead port so a developer running
        # Ollama locally gets the same fast refusal as CI.
        env={**os.environ, "OLLAMA_HOST": "http://127.0.0.1:1"},
    )
    assert result.returncode == 0, result.stderr


def _model_entry(name, family):
    return SimpleNamespace(model=name, details=SimpleNamespace(family=family))


_STUB_CONTEXT_LENGTH = 8192


class _OllamaStub:
    """A stand-in ollama module that records what the provider asked it for.

    Stubbing the module rather than patching ``update_chat_model_list`` keeps
    the real method, the real flag write and the real exception path under
    test; patching the method verifies only that the property's guard
    short-circuits against a fake that cooperates with it.

    It implements both surfaces the real package exposes, module-level
    ``list``/``show`` and ``Client``, and routes them into the same recorders.
    A stub carrying only ``Client`` lets module-level enumeration raise
    AttributeError into the provider's broad except, where it reads as "never
    enumerated" and hides the very regression these tests exist for.

    ``show`` answers from the entries the test queued rather than a parallel
    mapping, so the metadata can never disagree with the listing.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.list_calls = 0
        self.shown = []
        self.client_kwargs = []
        self.client_hosts = []
        self.show_failures = {}
        self._families = {}
        for outcome in self._responses:
            for entry in getattr(outcome, "models", None) or []:
                self._families[entry.model] = entry.details.family
        self.module = ModuleType("ollama")
        self.module.Client = self._make_client()
        self.module.list = self._list
        self.module.show = self._show

    def _list(self):
        self.list_calls += 1
        if not self._responses:
            raise AssertionError("unexpected extra ollama list() call")
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _show(self, model):
        self.shown.append(model)
        failure = self.show_failures.get(model)
        if failure is not None:
            raise failure
        family = self._families[model]
        return SimpleNamespace(
            modelinfo={f"{family}.context_length": _STUB_CONTEXT_LENGTH}
        )

    def _make_client(self):
        outer = self

        class Client:
            def __init__(self, host=None, **kwargs):
                outer.client_hosts.append(host)
                outer.client_kwargs.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def list(self):
                return outer._list()

            def show(self, model):
                return outer._show(model)

        return Client

    def install(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ollama", self.module)
        return self


class TestOllamaChatModelEnumeration:
    """#427: the Ollama model list is built for a caller that asks for it.

    Before, the constructor built it, so every server start imported the
    ollama SDK and called the Ollama host whatever the configured provider
    was.
    """

    def _provider(self):
        from notebook_intelligence.llm_providers.ollama_llm_provider import (
            OllamaLLMProvider,
        )

        return OllamaLLMProvider()

    def _stub(self, monkeypatch, responses):
        return _OllamaStub(responses).install(monkeypatch)

    def test_enumeration_happens_on_first_access_then_caches(self, monkeypatch):
        """Ordering is the point: not enumerated, then enumerated, then cached.

        A bare "exactly one call in total" assertion holds for the old
        constructor-side enumeration too, so it cannot tell the bug from the
        fix. The stub records both call surfaces, so a constructor cannot
        enumerate unnoticed by reaching for the other one.
        """
        stub = self._stub(monkeypatch, [SimpleNamespace(models=[])])
        provider = self._provider()
        assert stub.list_calls == 0

        _ = provider.chat_models
        assert stub.list_calls == 1

        _ = provider.chat_models
        assert stub.list_calls == 1

    def test_enumeration_bounds_each_request(self, monkeypatch):
        """Pins the value, because a timeout that merely exists can be an hour.

        The capabilities handler is synchronous, so this runs on the event-loop
        thread, and ollama's own default is timeout=None: on a host that drops
        packets that means the OS connect timeout, 75s on macOS.
        """
        from notebook_intelligence.llm_providers import ollama_llm_provider as provider_mod

        stub = self._stub(monkeypatch, [SimpleNamespace(models=[])])

        _ = self._provider().chat_models

        assert stub.client_kwargs == [
            {"timeout": provider_mod.OLLAMA_ENUMERATION_TIMEOUT_S}
        ]
        # Passed by keyword: positionally it would land on the SDK's `host`
        # parameter and leave the timeout unset.
        assert stub.client_hosts == [None]

    def test_returns_chat_models_and_skips_embedding_families(self, monkeypatch):
        """Two chat families, so a hardcoded context-window key cannot pass,
        and both embedding families, so neither entry goes unexercised."""
        listed = SimpleNamespace(
            models=[
                _model_entry("llama3", "llama"),
                _model_entry("qwen3", "qwen3moe"),
                _model_entry("nomic-embed", "nomic-bert"),
                _model_entry("bge", "bert"),
            ]
        )
        stub = self._stub(monkeypatch, [listed])

        models = self._provider().chat_models

        assert [m.id for m in models] == ["llama3", "qwen3"]
        assert [m.context_window for m in models] == [_STUB_CONTEXT_LENGTH] * 2
        assert stub.shown == ["llama3", "qwen3"]

    def test_one_unreadable_model_does_not_lose_the_others(self, monkeypatch):
        """The per-model guard. Metadata for a single model can fail on its
        own (a slow host now that the call is bounded, or a response without
        the context-length key) and must not cost the whole list."""
        listed = SimpleNamespace(
            models=[_model_entry("llama3", "llama"), _model_entry("qwen3", "qwen3moe")]
        )
        stub = self._stub(monkeypatch, [listed])
        stub.show_failures["llama3"] = RuntimeError("timed out")

        models = self._provider().chat_models

        assert [m.id for m in models] == ["qwen3"]

    def test_budget_stops_enumeration_and_reports_the_shortfall(
        self, monkeypatch, caplog
    ):
        """A host that answers the listing and then stalls per model cost one
        timeout per model on the event loop. Past the budget the rest are left
        out, and the warning has to say so rather than the list going quietly
        short."""
        import logging

        from notebook_intelligence.llm_providers import ollama_llm_provider as provider_mod

        listed = SimpleNamespace(
            models=[_model_entry("llama3", "llama"), _model_entry("qwen3", "qwen3moe")]
        )
        stub = self._stub(monkeypatch, [listed])
        monkeypatch.setattr(provider_mod, "OLLAMA_ENUMERATION_BUDGET_S", 0.0)

        with caplog.at_level(logging.WARNING, logger=provider_mod.__name__):
            models = self._provider().chat_models

        assert models == []
        assert stub.shown == []
        assert "short 2 model(s)" in caplog.text

    def test_unreachable_host_is_not_retried_per_access(self, monkeypatch):
        """A down host must not reconnect and re-log on every access.

        The property is read by the capabilities response, so retrying would
        put the connection attempt and its warning on every page load.
        """
        stub = self._stub(monkeypatch, [RuntimeError("Failed to connect to Ollama")])
        provider = self._provider()

        assert provider.chat_models == []
        assert provider.chat_models == []
        assert stub.list_calls == 1

    def test_explicit_refresh_recovers_after_a_miss(self, monkeypatch):
        """Pins recovery by its value, not by an empty list that a discarded
        refresh would also produce."""
        stub = self._stub(
            monkeypatch,
            [
                RuntimeError("Failed to connect to Ollama"),
                SimpleNamespace(models=[_model_entry("llama3", "llama")]),
            ],
        )
        provider = self._provider()
        assert provider.chat_models == []

        # A plain access must not recover on its own: that is what keeps a
        # down host from reconnecting per capabilities GET.
        assert provider.chat_models == []
        assert stub.list_calls == 1

        provider.update_chat_model_list()

        assert [m.id for m in provider.chat_models] == ["llama3"]

    def test_failed_refresh_keeps_the_last_good_list(self, monkeypatch):
        """The dropdown should not empty out because one refresh failed."""
        stub = self._stub(
            monkeypatch,
            [
                SimpleNamespace(models=[_model_entry("llama3", "llama")]),
                RuntimeError("Failed to connect to Ollama"),
            ],
        )
        provider = self._provider()
        assert [m.id for m in provider.chat_models] == ["llama3"]

        provider.update_chat_model_list()

        assert [m.id for m in provider.chat_models] == ["llama3"]
        assert stub.list_calls == 2


@pytest.fixture
def stub_litellm(monkeypatch):
    """Keep the env-contract tests off litellm's real import.

    litellm reads LITELLM_LOCAL_MODEL_COST_MAP only once, at its first import,
    so its behavior is not re-testable in-process anyway; the stub also keeps
    the opt-out test from triggering litellm's remote cost-map fetch when it
    happens to run as the first litellm import of the session.
    """
    monkeypatch.setitem(sys.modules, "litellm", ModuleType("litellm"))


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
