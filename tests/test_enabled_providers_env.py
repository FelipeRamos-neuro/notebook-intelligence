# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Issue #435: NBI_ENABLED_PROVIDERS has to tolerate whitespace.

`is_provider_enabled_in_env` split the variable on `,` with no strip, so
`"github-copilot, ollama"` (the natural way to write a list) re-enabled only
`github-copilot` and left `ollama` hidden with no error anywhere. A stray
space around a single entry disabled it outright.

The variable is only consulted when an admin has turned on
`allow_enabling_providers_with_env`, and its sole consumer is the
`is_provider_enabled` closure in the capabilities handler
(`extension.py:663`). That handler has no test harness here, so these cover
the parsing function it calls.

Nothing covered this function or the variable before.
"""

import pytest
from notebook_intelligence.util import is_provider_enabled_in_env


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # A value left in the developer's shell would mask every case below.
    monkeypatch.delenv("NBI_ENABLED_PROVIDERS", raising=False)


def _set(monkeypatch, raw):
    monkeypatch.setenv("NBI_ENABLED_PROVIDERS", raw)


class TestEnabledProvidersParsing:
    def test_comma_separated_enables_every_entry(self, monkeypatch):
        _set(monkeypatch, "github-copilot,ollama")

        assert is_provider_enabled_in_env("github-copilot") is True
        assert is_provider_enabled_in_env("ollama") is True

    def test_comma_space_enables_every_entry(self, monkeypatch):
        # The reported regression: this form silently dropped `ollama`.
        _set(monkeypatch, "github-copilot, ollama")

        assert is_provider_enabled_in_env("github-copilot") is True
        assert is_provider_enabled_in_env("ollama") is True

    @pytest.mark.parametrize(
        "raw", [" ollama ", "ollama ", " ollama", "\tollama\t", "ollama\n"]
    )
    def test_surrounding_whitespace_is_ignored(self, monkeypatch, raw):
        # A single stray space used to disable the only entry in the list.
        _set(monkeypatch, raw)

        assert is_provider_enabled_in_env("ollama") is True

    def test_trailing_and_repeated_commas_are_harmless(self, monkeypatch):
        _set(monkeypatch, "ollama,,github-copilot,")

        assert is_provider_enabled_in_env("ollama") is True
        assert is_provider_enabled_in_env("github-copilot") is True

    def test_a_provider_not_listed_is_not_enabled(self, monkeypatch):
        _set(monkeypatch, "github-copilot, ollama")

        assert is_provider_enabled_in_env("litellm-compatible") is False

    def test_unset_enables_nothing(self):
        assert is_provider_enabled_in_env("ollama") is False

    @pytest.mark.parametrize("raw", ["", "   ", ",", " , "])
    def test_blank_values_enable_nothing(self, monkeypatch, raw):
        _set(monkeypatch, raw)

        assert is_provider_enabled_in_env("ollama") is False

    @pytest.mark.parametrize("raw", ["", "   ", ",", "ollama"])
    def test_an_empty_provider_id_is_never_enabled(self, monkeypatch, raw):
        """Closes a fail-open that predates the whitespace bug.

        ``''.split(',')`` is ``['']``, so an empty provider id matched an
        unset variable and read as enabled. Unreachable today because no
        registered provider has an empty id, but it is the wrong default for
        a re-enable check.
        """
        _set(monkeypatch, raw)

        assert is_provider_enabled_in_env("") is False

    def test_matching_stays_case_sensitive(self, monkeypatch):
        # Provider ids are exact strings elsewhere (`llm_providers` keys), so
        # loosening case here would make the denylist and the re-enable list
        # disagree about what a provider is called.
        _set(monkeypatch, "OLLAMA")

        assert is_provider_enabled_in_env("ollama") is False
        assert is_provider_enabled_in_env("OLLAMA") is True

    def test_internal_whitespace_is_not_stripped(self, monkeypatch):
        # Only surrounding whitespace is noise; an id with an inner space is
        # simply not a real provider id and must not match one.
        _set(monkeypatch, "git hub-copilot")

        assert is_provider_enabled_in_env("github-copilot") is False
        assert is_provider_enabled_in_env("git hub-copilot") is True
