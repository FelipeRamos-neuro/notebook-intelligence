# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Issue #431: `disabled_providers` has to suppress a provider's models,
not just the provider entry.

The capabilities response filtered `llm_providers` through the enablement
predicate while `chat_models` / `inline_completion_models` /
`embedding_models` came straight from `AIServiceManager`, which walks
every registered provider. An admin's denylist therefore removed the
provider from the picker while its models stayed in the payload, and for
the Ollama provider enumerating them means calling the Ollama host, so
the response did work for a provider that was switched off.

These cover the extracted predicate-driven filter. The three call sites in
`GetCapabilitiesHandler.get` are one-liners around it and are not exercised
here, because the handler has no test harness in this repo; the same
reproduce-the-expression-in-isolation limitation is documented in
`tests/test_config_integration.py`.
"""

from notebook_intelligence.util import filter_models_by_enabled_providers


def _model(provider, model_id):
    # Mirrors the shape built by AIServiceManager.chat_model_ids.
    return {
        "provider": provider,
        "id": model_id,
        "name": model_id,
        "context_window": 8192,
        "properties": [],
    }


ALL_ENABLED = lambda _provider_id: True  # noqa: E731 - test data, not logic


class TestFilterModelsByEnabledProviders:
    def test_drops_models_of_a_disabled_provider(self):
        models = [
            _model("ollama", "llama3"),
            _model("github-copilot", "gpt-4o"),
            _model("ollama", "qwen3"),
        ]

        kept = filter_models_by_enabled_providers(
            models, lambda provider_id: provider_id != "ollama"
        )

        assert [m["id"] for m in kept] == ["gpt-4o"]

    def test_keeps_every_model_when_nothing_is_disabled(self):
        # The `disabled_providers is None` case in the caller's predicate:
        # the payload must be byte-identical to the unfiltered list, since
        # this is the default for every install.
        models = [_model("ollama", "llama3"), _model("github-copilot", "gpt-4o")]

        kept = filter_models_by_enabled_providers(models, ALL_ENABLED)

        assert kept == models

    def test_honors_a_re_enabled_provider(self):
        # The caller's predicate folds in the per-pod re-enable env var, so a
        # provider that is denylisted but re-enabled must keep its models.
        # Taking the predicate rather than the denylist is what makes this
        # work without duplicating that resolution.
        models = [_model("ollama", "llama3")]

        kept = filter_models_by_enabled_providers(models, ALL_ENABLED)

        assert [m["id"] for m in kept] == ["llama3"]

    def test_drops_all_models_when_the_only_provider_is_disabled(self):
        models = [_model("ollama", "llama3"), _model("ollama", "qwen3")]

        kept = filter_models_by_enabled_providers(
            models, lambda provider_id: provider_id != "ollama"
        )

        assert kept == []

    def test_keeps_an_entry_whose_provider_cannot_be_read(self):
        """Fails open on an unreadable entry, deliberately.

        Every entry is built in-process with a `provider` key, so this only
        matters if that shape changes. Hiding a model for a reason the admin
        did not ask for is the worse failure, and it would be silent.
        """
        models = [
            {"id": "no-provider-key", "name": "odd"},
            {"provider": None, "id": "null-provider", "name": "odd"},
            "not-a-dict",
            _model("ollama", "llama3"),
        ]

        kept = filter_models_by_enabled_providers(
            models, lambda provider_id: provider_id != "ollama"
        )

        assert [
            m.get("id") if isinstance(m, dict) else m for m in kept
        ] == ["no-provider-key", "null-provider", "not-a-dict"]

    def test_non_list_input_degrades_to_empty(self):
        # Matches split_csv's posture in the same module: a malformed value
        # must not crash the capabilities response.
        for bad in (None, "chat_models", 42, {"provider": "ollama"}):
            assert filter_models_by_enabled_providers(bad, ALL_ENABLED) == []

    def test_does_not_mutate_the_input_list(self):
        # The properties hand back live lists from the manager; filtering must
        # not disturb what the caller holds.
        models = [_model("ollama", "llama3"), _model("github-copilot", "gpt-4o")]
        snapshot = list(models)

        filter_models_by_enabled_providers(
            models, lambda provider_id: provider_id != "ollama"
        )

        assert models == snapshot
