"""The NBI_TOUR_DISABLED switch: env var wins over the traitlet."""

import pytest

from notebook_intelligence import extension as ext_module


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("NBI_TOUR_DISABLED", raising=False)


def test_default_is_enabled():
    assert ext_module.NotebookIntelligence().tour_disabled is False
    assert ext_module.GetCapabilitiesHandler.tour_disabled is False


def test_traitlet_disables():
    assert ext_module._resolve_bool_with_env("NBI_TOUR_DISABLED", True) is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_env_var_disables(monkeypatch, value):
    monkeypatch.setenv("NBI_TOUR_DISABLED", value)
    assert ext_module._resolve_bool_with_env("NBI_TOUR_DISABLED", False) is True


def test_env_var_overrides_traitlet(monkeypatch):
    monkeypatch.setenv("NBI_TOUR_DISABLED", "0")
    assert ext_module._resolve_bool_with_env("NBI_TOUR_DISABLED", True) is False


def test_unrecognized_env_value_fails_loudly(monkeypatch):
    monkeypatch.setenv("NBI_TOUR_DISABLED", "maybe")
    with pytest.raises(ValueError):
        ext_module._resolve_bool_with_env("NBI_TOUR_DISABLED", False)
